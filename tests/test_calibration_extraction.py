"""General extraction regressions discovered during blind resume calibration."""

import unittest

from backend import matching_extraction as extraction
from backend import deterministic_scoring as scoring
from datetime import date


class CalibrationExtractionTests(unittest.TestCase):
    def test_degree_abbreviation_is_an_education_requirement_without_word_degree(self):
        result = scoring.score("Education\nB.Tech Computer Science", "Requirements\nB.Tech/M.Tech in Computer Science",
                               0.5, as_of_date=date(2026, 11, 16))
        self.assertEqual(result["components"]["education_title"], 1)

    def test_hypothetical_prior_background_is_preferred_not_mandatory(self):
        row, = extraction.extract_jd_requirements("Requirements\nYou may have worked with Google Cloud")
        self.assertEqual(row["priority"], "nice_to_have")
        required, = extraction.extract_jd_requirements("Requirements\nYou may need to use Google Cloud")
        self.assertEqual(required["priority"], "must_have")

    def test_recruiting_fraud_footer_does_not_create_recruiting_requirement(self):
        rows = extraction.extract_jd_requirements("Requirements\nPython\nRecruitment Fraud Alert!\nRecruiting agencies must not contact us")
        self.assertEqual({skill for row in rows for skill in row["skills"]}, {"Python"})

    def test_optional_tenure_evidence_is_numeric_without_mandatory_tenure(self):
        result = scoring.score("Projects\nBuilt Python services", "Requirements\nPython\nPreferred qualifications\n3 years experience",
                               0.5, as_of_date=date(2026, 11, 16))
        clause = next(row for row in result["requirement_evidence"] if "3 years" in row["jd"])
        self.assertEqual(clause["criteria"][0]["status"], "no_evidence")
        self.assertFalse(result["experience_hard_cap_applied"])

    def test_preferably_context_does_not_demote_main_tenure_requirement(self):
        rows = extraction.extract_jd_requirements(
            "Requirements\n10+ years in software development, preferably in a startup environment")
        self.assertEqual(next(row for row in rows if row["minimum_years"] is not None)["priority"], "must_have")
        self.assertEqual(next(row for row in rows if "startup" in row["text"])["priority"], "nice_to_have")

    def test_qualification_heading_variants_escape_company_background(self):
        for heading in ("Expectations/ Requirements", "Essential Skills",
                        "Superpowers/ Skills that will help you succeed in this role",
                        "Qualifications and Education Requirements", "Skills that will help you succeed in this role"):
            with self.subTest(heading=heading):
                rows = extraction.extract_jd_requirements("About Us\nCompany background\n" + heading + "\nPython required")
                self.assertTrue(any(row["priority"] == "must_have" and "Python" in row["skills"] for row in rows))
        for heading in ("Role and Responsibilities", "What You'll Achieve", "Daily Adventures & Responsibilities"):
            with self.subTest(heading=heading):
                sections = extraction.split_sections("About Us\nCompany background\n" + heading + "\nBuild Python services")
                self.assertEqual(sections[-1]["name"], "responsibilities")
        sections = extraction.split_sections("Good-to-Have Skills\nPython")
        self.assertEqual(sections[0]["name"], "nice_to_have")

    def test_hyphen_to_tenure_range_keeps_lower_bound(self):
        self.assertEqual(extraction.explicit_years_experience("Relevant industry experience of 3-to 5 years"), [3])

    def test_ai_vocabulary_cannot_establish_customer_success_tenure(self):
        resume = "Experience\nDeveloper Jan 2025 - Dec 2025\nBuilt Python AI applications"
        result = scoring.score(resume,
                               "Requirements\n5+ years in customer success or strategic account management\nAI tools",
                               0.9, as_of_date=date(2026, 11, 16))
        self.assertIn("Customer success", result["missing_must_have"])
        self.assertEqual(result["experience_evidence"][0]["observed_years"], 0)

    def test_completed_degree_does_not_prove_current_enrollment(self):
        jd = "Requirements\nCurrently pursuing a Bachelor's degree in Computer Science\nPython"
        resume = "Projects\nBuilt Python services\nEducation\nUniversity\nAug 2022 - May 2026\nB.Tech Computer Science"
        result = scoring.score(resume, jd, 0.9, as_of_date=date(2026, 11, 16))
        self.assertLessEqual(result["score"], 40)
        self.assertEqual(result["components"]["education_title"], 0)
        self.assertTrue(any(item["reason"] == "current_enrollment" for item in result["missing_must_have_requirements"]))
        active = scoring.score(resume, jd, 0.9, as_of_date=date(2026, 4, 16))
        self.assertGreater(active["score"], 40)
        self.assertNotEqual(scoring.cache_version(resume, date(2026, 4, 16), "model"),
                            scoring.cache_version(resume, date(2026, 11, 16), "model"))

    def test_enrollment_needs_relevant_degree_and_evidence(self):
        self.assertEqual(extraction.education_enrollment_evidence("Education\nB.Tech Computer Science", date(2026, 11, 16)), [])
        self.assertEqual(extraction.education_enrollment_evidence("Education\nB.Tech Computer Science\nJan 2027 - Dec 2030", date(2026, 11, 16)), [])
        result = scoring.score("Education\nPhD Computer Science\nJan 2018 - Dec 2022\n\n\n"
                               "B.Tech Computer Science\nJan 2025 - Dec 2028",
                               "Requirements\nCurrently pursuing a PhD in Computer Science", 0.8,
                               as_of_date=date(2026, 11, 16))
        self.assertTrue(any(item["reason"] == "current_enrollment" for item in result["missing_must_have_requirements"]))

    def test_bracketed_and_plus_upper_range_years_keep_lower_bound(self):
        for text, expected in (("[8+] years closing business", 8),
                               ("3-5+ years backend engineering", 3)):
            with self.subTest(text=text):
                row, = extraction.extract_jd_requirements("Requirements\n" + text)
                self.assertEqual(row["minimum_years"], expected)

    def test_role_specific_qualifications_do_not_disappear(self):
        rows = extraction.extract_jd_requirements(
            "What do we expect from you?\n7-10 years backend engineering\n"
            "Experience managing people managers\nExperience in the video games industry\n"
            "Experience with 802.11 and wireless drivers")
        self.assertTrue(all(row["priority"] == "must_have" for row in rows))
        self.assertTrue({"Backend engineering", "People management", "Game development", "Wireless networking"}
                        .issubset({skill for row in rows for skill in row["skills"]}))

    def test_lowercase_react_requires_technical_framework_context(self):
        technical = extraction.extract_skill_mentions("Requirements\nExperience with front-end frameworks such as react")
        ordinary = extraction.extract_skill_mentions("We react quickly to customer needs")
        self.assertIn("React", {row["skill"] for row in technical})
        self.assertNotIn("React", {row["skill"] for row in ordinary})

    def test_reviewed_data_tools_keep_short_names_but_not_ordinary_spark(self):
        names = {row["skill"] for row in extraction.extract_skill_mentions("Requirements\nSpark, Airflow, Cassandra")}
        self.assertTrue({"Spark", "Airflow", "Cassandra"}.issubset(names))
        self.assertNotIn("Spark", {row["skill"] for row in extraction.extract_skill_mentions("A spark of innovation")})
    def test_parenthetical_bonus_does_not_demote_the_required_main_clause(self):
        rows = extraction.extract_jd_requirements("Requirements\nExperience programming in Python (Java is a plus)")
        self.assertEqual(next(row for row in rows if "Python" in row["text"])["priority"], "must_have")
        self.assertEqual(next(row for row in rows if "Java" in row["text"])["priority"], "nice_to_have")
        self.assertTrue(all(row["line"] == 2 for row in rows))

    def test_or_more_years_and_people_management_are_explicit_requirements(self):
        row, = extraction.extract_jd_requirements("Requirements\n6 or more years of managing engineering teams")
        self.assertEqual(row["minimum_years"], 6)
        self.assertIn("People management", row["skills"])

    def test_explicit_react_library_name_in_project_stack_is_not_a_verb(self):
        rows = extraction.extract_skill_mentions("Projects\nJournal — Python · React.js · Firebase\nBuilt a journal app")
        self.assertTrue(any(row["skill"] == "React" and row["evidence_credit"] >= 0.75 for row in rows))

    def test_repeated_clause_does_not_inflate_its_share_of_the_score(self):
        resume = "Experience\nBuilt Python services"
        jd = "Requirements\nPython\nSQL"
        first = scoring.score(resume, jd, 0.6, as_of_date=date(2026, 11, 16))
        repeated = scoring.score(resume, jd + "\nPython\nPython", 0.6, as_of_date=date(2026, 11, 16))
        self.assertEqual(first["components"], repeated["components"])
        self.assertEqual(first["score"], repeated["score"])

    def test_topical_similarity_cannot_prove_missing_role_capability(self):
        result = scoring.score("Experience\nBuilt Python services", "Requirements\nSales experience", 0.99,
                               as_of_date=date(2026, 11, 16))
        self.assertEqual(result["components"]["semantic"], 0)
        self.assertEqual(result["components"]["experience"], 0)

    def test_clause_rubric_weights_preserve_total_skills_budget(self):
        for jd, expected in (("Requirements\nPython", (40, 0)),
                             ("Requirements\nPython\nBonus\nDocker", (30, 10))):
            result = scoring.score("Experience\nBuilt Python services", jd, 0.5,
                                   as_of_date=date(2026, 11, 16))
            self.assertEqual((result["weights"]["must_have"], result["weights"]["nice_to_have"]), expected)
            self.assertEqual(result["weights"]["experience"], 30)
            self.assertEqual(result["weights"]["semantic"], 20)
            self.assertEqual(sum(result["weights"].values()), 100)

    def test_company_intro_does_not_hide_later_job_responsibilities(self):
        text = ("About Example\nCompany background\nJob Responsibilities\nBuild Python services\n"
                "What are we looking for ?\n3+ years of Python experience")
        sections = extraction.split_sections(text)
        self.assertEqual([row["name"] for row in sections],
                         ["non_requirements", "responsibilities", "requirements"])
        requirements = extraction.extract_jd_requirements(text)
        self.assertTrue(any(row["minimum_years"] == 3 and row["priority"] == "must_have" for row in requirements))
        variant = extraction.split_sections("About Us\nCompany\nWhat You’ll Be Doing\nBuild Python services\n"
                                            "What You’ll Bring To The Role\nPython\nWhy You’ll Love It Here\nHealth coverage")
        self.assertEqual([row["name"] for row in variant],
                         ["non_requirements", "responsibilities", "requirements", "non_requirements"])

    def test_unicode_experience_ranges_use_the_lower_bound(self):
        for separator in ("-", "\u2013", "\u2014", " to "):
            row, = extraction.extract_jd_requirements("Requirements\n2" + separator + "5 years experience")
            self.assertEqual(row["minimum_years"], 2)

    def test_interest_in_learning_does_not_require_existing_infrastructure_experience(self):
        result = scoring.score("Experience\nBuilt Python services",
                               "Requirements\nPython\nAre excited to explore systems, from infrastructure to UI", 0.6,
                               as_of_date=date(2026, 11, 16))
        self.assertNotIn("Infrastructure engineering", result["missing_must_have"])

    def test_classifier_fine_tuning_is_ml_evidence_but_process_tuning_is_not(self):
        positive = extraction.extract_skill_mentions("Projects\nFine-tuned a Transformer classifier using training data")
        negative = extraction.extract_skill_mentions("Experience\nFine-tuned recruiting processes")
        self.assertIn("Machine learning", {row["skill"] for row in positive})
        self.assertNotIn("Machine learning", {row["skill"] for row in negative})

    def test_language_delivery_supports_generic_programming_but_not_recruiting(self):
        resume = "Experience\nBuilt Python services"
        result = scoring.score(resume, "Requirements\nExperience programming with at least one language", 0.6,
                               as_of_date=date(2026, 11, 16))
        self.assertEqual(result["missing_must_have"], [])
        self.assertGreaterEqual(result["components"]["must_have"], 0.75)
        unrelated = scoring.score(resume, "Requirements\n3+ years of recruiting experience", 0.6,
                                  as_of_date=date(2026, 11, 16))
        self.assertIn("Recruiting", unrelated["missing_must_have"])
        self.assertEqual(unrelated["experience_evidence"][0]["observed_years"], 0)

    def test_numbered_collections_requirements_do_not_become_software_skills(self):
        result = scoring.score("Experience\nBuilt Python services",
                               "8. Key Skills & Competencies\nStrong expertise in collections strategy", 0.3,
                               as_of_date=date(2026, 11, 16))
        self.assertIn("Debt collections", result["missing_must_have"])
        self.assertEqual(result["components"]["experience"], 0)

    def test_degree_abbreviations_do_not_match_ordinary_words(self):
        for sentence in ("Base pay will be determined individually", "Contact me in writing"):
            self.assertEqual(extraction.extract_education(sentence), [])
        self.assertIn("bachelors", extraction.extract_education("B.E. in Computer Science"))
        self.assertIn("bachelors", extraction.extract_education("BE Computer Science"))
        self.assertIn("masters", extraction.extract_education("M.E. in Engineering"))

    def test_flattened_required_degree_is_not_made_optional_by_later_plus(self):
        jd = ("Qualifications and Skills: You are pursuing a PhD degree in Computer Science "
              "You have excellent programming skills in Python (Java is a plus) "
              "You have strong communication skills")
        rows = extraction.extract_jd_requirements(jd)
        degree, = [row for row in rows if "PhD" in row["text"]]
        self.assertEqual(degree["priority"], "must_have")
        self.assertEqual(degree["line"], 1)
        result = scoring.score("Education\nB.Tech Computer Science\nProjects\nBuilt Python services", jd,
                               0.8, as_of_date=date(2026, 11, 16))
        self.assertLessEqual(result["score"], 30)

    def test_android_does_not_satisfy_ios_requirements(self):
        result = scoring.score("Experience\nBuilt Android apps using Kotlin", "Requirements\nDeep iOS engineering experience",
                               0.5, as_of_date=date(2026, 11, 16))
        self.assertIn("iOS", result["missing_must_have"])
        self.assertLessEqual(result["score"], 30)

    def test_large_explicit_tenure_gap_reduces_overall_readiness(self):
        resume = "Experience\nDeveloper Jan 2025 - Dec 2025\nBuilt Python services"
        junior = scoring.score(resume, "Requirements\nPython\n3+ years experience", 0.8,
                               as_of_date=date(2026, 11, 16))
        senior = scoring.score(resume, "Requirements\nPython\n10+ years experience", 0.8,
                               as_of_date=date(2026, 11, 16))
        self.assertGreater(junior["score"] - senior["score"], 15)
        experienced = scoring.score("Experience\nDeveloper Jan 2010 - Dec 2025\nBuilt Python services",
                                    "Requirements\nPython\n10+ years experience", 0.8,
                                    as_of_date=date(2026, 11, 16))
        self.assertGreater(experienced["score"], junior["score"])

    def test_common_qualification_headings_keep_requirements_and_optional_skills_separate(self):
        for heading in ("About you", "We’d love to hear from you if you have:"):
            with self.subTest(heading=heading):
                rows = extraction.extract_jd_requirements(
                    heading + "\nExperience with Python\n"
                    "While it’s not required, it’s an added plus if you also have:\nExperience with Rust")
                by_skill = {skill: row["priority"] for row in rows for skill in row["skills"]}
                self.assertEqual(by_skill["Python"], "must_have")
                self.assertEqual(by_skill["Rust"], "nice_to_have")

    def test_project_stack_is_demonstrated_but_skills_list_is_listed_only(self):
        project = extraction.extract_skill_mentions("Projects\nJournal — Python · PyTorch\nBuilt a classifier.")
        listed = extraction.extract_skill_mentions("Technical skills\nPython, PyTorch")
        self.assertTrue(all(row["evidence_credit"] == 0.75 for row in project))
        self.assertTrue(all(row["evidence_credit"] == 0.4 for row in listed))

    def test_lone_project_technology_does_not_become_a_demonstrated_stack(self):
        row, = extraction.extract_skill_mentions("Projects\nReading — Python")
        self.assertEqual(row["evidence_credit"], 0.1)

    def test_location_and_pay_do_not_add_technical_requirements(self):
        rows = extraction.extract_jd_requirements(
            "Requirements\nPython\nWhere You'll Be\nRust required in London\n"
            "Pay Transparency Disclosure\nJava required")
        self.assertEqual({skill for row in rows for skill in row["skills"]}, {"Python"})

    def test_employer_product_mentions_are_not_tool_requirements(self):
        for sentence in ("At Figma, one of our values is Grow as you go", "What you'll do at Figma:"):
            self.assertNotIn("Figma", {m["skill"] for m in extraction.extract_skill_mentions(sentence)})
        self.assertIn("Figma", {m["skill"] for m in extraction.extract_skill_mentions("Experience with Figma")})
        self.assertIn("Figma", {m["skill"] for m in extraction.extract_skill_mentions("Skills\nPython, Figma")})

    def test_preferably_is_optional(self):
        row, = extraction.extract_jd_requirements("Requirements\nYou preferably have experience with AWS")
        self.assertEqual(row["priority"], "nice_to_have")

    def test_example_lists_and_similar_languages_do_not_require_every_language(self):
        for clause in ("Experience in Python, Scala, or similar languages",
                       "Front-end frameworks (e.g., React/TypeScript) and back-end technologies (e.g., Ruby, Python, Go, C++)"):
            with self.subTest(clause=clause):
                result = scoring.score("Projects\nBuilt Python services using React", "Requirements\n" + clause,
                                       0.6, as_of_date=date(2026, 11, 16))
                self.assertEqual(result["missing_must_have"], [])
        conjunction = scoring.score("Projects\nBuilt Python services", "Requirements\nPython and Go",
                                    0.6, as_of_date=date(2026, 11, 16))
        self.assertEqual(conjunction["missing_must_have"], ["Go"])

    def test_missing_mandatory_doctorate_cannot_be_hidden_by_matching_keywords(self):
        result = scoring.score("Projects\nBuilt Python PyTorch models\nEducation\nB.Tech Computer Science",
                               "Requirements\nPh.D student required\nPython\nPyTorch", 0.9,
                               as_of_date=date(2026, 11, 16))
        self.assertLessEqual(result["score"], 30)
        self.assertTrue(result["hard_cap_applied"])
        optional = scoring.score("Projects\nBuilt Python PyTorch models\nEducation\nB.Tech Computer Science",
                                 "Requirements\nPython\nPyTorch\nPreferred qualifications\nPh.D degree", 0.9,
                                 as_of_date=date(2026, 11, 16))
        self.assertGreater(optional["score"], 30)

    def test_unsupported_project_stack_cannot_inflate_demonstrated_evidence(self):
        rows = extraction.extract_skill_mentions("Projects\nWish list — Python · PyTorch")
        self.assertTrue(all(row["evidence_credit"] == 0.1 for row in rows))

    def test_unrelated_work_does_not_receive_required_skill_experience_credit(self):
        result = scoring.score("Experience\nBuilt Python services", "Requirements\nSales experience", 0.4,
                               as_of_date=date(2026, 11, 16))
        self.assertEqual(result["components"]["experience"], 0.0)


if __name__ == "__main__":
    unittest.main()
