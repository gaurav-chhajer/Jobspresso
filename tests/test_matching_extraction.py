import unittest
from datetime import date

from backend import matching_extraction as extraction


class MatchingExtractionTests(unittest.TestCase):
    def test_bonus_is_optional_and_location_is_not_a_requirement(self):
        text = ("Requirements\nNative Android development with Kotlin and Jetpack Compose\n"
                "Have designed and run A/B tests\nBonus\n"
                "Experience with audio playback or streaming on Android\n"
                "Experience building mobile infrastructure for build, test and release pipelines\n"
                "Location\nThis role is remote-first with GMT to GMT+4 timezones preferred.\n"
                "If you prefer, work from London or Paris.\n#LI-Remote")
        items = extraction.extract_jd_requirements(text)
        self.assertEqual(len(items), 4)
        self.assertEqual([item["priority"] for item in items],
                         ["must_have", "must_have", "nice_to_have", "nice_to_have"])
        self.assertEqual(items[1]["skills"], ["A/B testing"])
        self.assertIn("Audio playback", items[2]["skills"])
        self.assertIn("Audio streaming", items[2]["skills"])
        self.assertIn("CI/CD", items[3]["skills"])
        self.assertEqual(items[3]["line"], 6)

    def test_inline_location_and_standalone_ats_tags_are_excluded(self):
        items = extraction.extract_jd_requirements(
            "Requirements: Kotlin required. Bonus: Docker. Location: Python required in London.")
        self.assertEqual([(item["skills"], item["priority"]) for item in items],
                         [(["Kotlin"], "must_have"), (["Docker"], "nice_to_have")])
        self.assertEqual(len(extraction.extract_jd_requirements("Requirements\nKotlin\n#LI-Remote")), 1)

    def test_sentence_verbs_are_not_unknown_technologies(self):
        for clause in ("Care deeply about developer experience and engineering productivity",
                       "Write clean, maintainable code and enjoy owning systems end-to-end"):
            with self.subTest(clause=clause):
                self.assertEqual(extraction.extract_jd_requirements("Requirements\n" + clause)[0]["unknown_skills"], [])
        unknown, = extraction.extract_jd_requirements("Requirements\nAcmeCloud platform experience required")
        self.assertEqual(unknown["unknown_skills"], ["AcmeCloud"])

    def test_capabilities_keep_partial_transfer_and_contextual_delivery_evidence(self):
        mentions = extraction.expand_implied_skills(extraction.extract_skill_mentions(
            "Projects\nDeployed services via GitHub Actions with automated builds."))
        by_skill = {item["skill"]: item for item in mentions}
        self.assertEqual(by_skill["CI/CD"]["evidence_credit"], 0.75)
        self.assertEqual(by_skill["Workflow automation"]["match_type"], "alias")
        self.assertEqual(by_skill["Developer tooling"]["evidence_credit"], 0.4)
        self.assertNotIn("Coding agents", by_skill)
        for text in ("Skills\nGitHub Actions", "Summary\nRead AI news", "No experience with coding agents"):
            names = {item["skill"] for item in extraction.extract_skill_mentions(text)}
            self.assertFalse(names & {"CI/CD", "Coding agents"})

    def test_ai_application_outcome_is_evidence_but_news_and_learning_are_not(self):
        mentions = extraction.extract_skill_mentions(
            "Projects\nIncreased engagement by 30% by building an AI journaling app.")
        self.assertEqual(next(item for item in mentions if item["skill"] == "Artificial intelligence")["evidence_credit"], 1)
        self.assertNotIn("Artificial intelligence", {item["skill"] for item in
            extraction.extract_skill_mentions("Summary\nRead AI news")})
        learning = extraction.extract_skill_mentions("Projects\nLearning about building an AI application.")
        self.assertLessEqual(next(item for item in learning if item["skill"] == "Artificial intelligence")["evidence_credit"], 0.1)

    def test_qualification_introductory_lines_are_not_clauses(self):
        items = extraction.extract_jd_requirements(
            "What We\u2019re Looking For\n"
            "We\u2019re looking for strong engineers who enjoy building systems that improve how software gets built.\n"
            "You might be a good fit if you:\nEnjoy designing systems that automate complex workflows")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["skills"], [])
        self.assertEqual(items[1]["skills"], ["Workflow automation"])

    def test_clay_curly_apostrophe_heading_keeps_unmapped_qualifications(self):
        items = extraction.extract_jd_requirements(
            "What You\u2019ll Work On\nCI/CD pipelines, including evaluation and validation.\n"
            "What We\u2019re Looking For\n"
            "Have experience building developer tooling, infrastructure, or internal platforms\n"
            "Enjoy designing systems that automate complex workflows\n"
            "Why This Role Is Unique\nSoftware engineering is changing quickly.")
        required = [item for item in items if item["priority"] == "must_have"]
        self.assertEqual(len(required), 2)
        self.assertEqual(required[0]["line"], 4)
        self.assertEqual(required[1]["line"], 5)
        self.assertNotIn("Software engineering is changing quickly.", [item["text"] for item in items])

    def test_skills_need_to_bring_heading_preserves_all_required_clauses(self):
        items = extraction.extract_jd_requirements(
            "ABOUT THE ROLE\nWe use TypeScript.\nSKILLS YOU'LL NEED TO BRING\n"
            "Over 10 years of experience.\n"
            "Experience building developer tooling (CI/CD).\n"
            "Strong collaboration and communication skills.\nBenefits\nPaid time off.")
        required = [item for item in items if item["priority"] == "must_have"]
        self.assertEqual(len(required), 3)
        self.assertEqual(required[0]["minimum_years"], 10)
        self.assertEqual(required[1]["skills"], ["CI/CD", "Developer tooling"])
        self.assertEqual(required[2]["text"], "Strong collaboration and communication skills.")
        self.assertNotIn("SKILLS YOU'LL NEED TO BRING", [item["text"] for item in items])

    def test_need_to_bring_scope_ends_before_company_policy_sections(self):
        for heading in ("A NOTE ON AI", "EQUAL OPPORTUNITY & ACCOMMODATIONS"):
            with self.subTest(heading=heading):
                items = extraction.extract_jd_requirements(
                    "SKILLS YOU'LL NEED TO BRING\nCI/CD experience.\n" + heading +
                    "\nWe encourage everyone to apply. Python curiosity is welcome.")
                self.assertEqual([item["text"] for item in items], ["CI/CD experience."])

    def test_optional_scope_and_negated_required_heading(self):
        items = extraction.extract_jd_requirements("Requirements: Python required. Additional Experience (Not Required): C++. Docker.")
        self.assertEqual([(q["skills"], q["priority"]) for q in items],
                         [(["Python"], "must_have"), (["C++"], "nice_to_have"), (["Docker"], "nice_to_have")])
        self.assertEqual(extraction.extract_jd_requirements("Requirements\nC++ is optional")[0]["priority"], "nice_to_have")
        multiline = extraction.extract_jd_requirements("Requirements\nPython\nAdditional Experience (Not Required):\nC++\nDocker")
        self.assertEqual([q["priority"] for q in multiline], ["must_have", "nice_to_have", "nice_to_have"])

    def test_room_growth_is_not_android_skill(self):
        self.assertEqual(extraction.extract_skill_mentions("Requirements\nRoom to grow professionally"), [])
        self.assertEqual(extraction.extract_skill_mentions("Requirements\nRoom database")[0]["skill"], "Room")

    def test_abbreviation_does_not_split_degree_clause(self):
        items = extraction.extract_jd_requirements("Requirements: Bachelor's degree in a related field (e.g. Information Technology). Python required.")
        self.assertEqual(len(items), 2)
        self.assertIn("Information Technology", items[0]["text"])

    def test_years_and_optional_priority_stay_with_local_bullet(self):
        items = extraction.extract_jd_requirements("Requirements: - At least 3 years of Python - Docker - SQL preferred")
        self.assertEqual([(q["skills"], q["minimum_years"], q["priority"]) for q in items],
                         [(["Python"], 3, "must_have"), (["Docker"], None, "must_have"), (["SQL"], None, "nice_to_have")])

    def test_experience_clauses_do_not_share_preferred_cue(self):
        items = extraction.extract_jd_requirements("Requirements: Strong Java development experience Experience with SQL preferred")
        self.assertEqual([(q["skills"], q["priority"]) for q in items],
                         [(["Java"], "must_have"), (["SQL"], "nice_to_have")])

    def test_flattened_required_and_preferred_sentences_keep_priorities(self):
        items = extraction.extract_jd_requirements("Python is required. Docker is preferred.")
        self.assertEqual([(item["skills"], item["priority"], item["line"]) for item in items],
                         [(["Python"], "must_have", 1), (["Docker"], "nice_to_have", 1)])

    def test_inline_headings_and_technical_dots(self):
        items = extraction.extract_jd_requirements(
            "Requirements: Node.js and .NET; Python. Preferred qualifications: Docker.")
        self.assertEqual(items[0]["skills"], [".NET", "Node.js"])
        self.assertEqual(items[1]["skills"], ["Python"])
        self.assertEqual([item["priority"] for item in items], ["must_have", "must_have", "nice_to_have"])

    def test_flattened_and_multiline_requirements_match(self):
        lines = "Requirements:\nPython required.\nDocker required.\nPreferred qualifications:\nAWS preferred."
        compact = lambda text: [(q["skills"], q["priority"]) for q in extraction.extract_jd_requirements(text)]
        self.assertEqual(compact(lines), compact(lines.replace("\n", " ")))

    def test_clauses_preserve_decimal_years_and_source_lines(self):
        items = extraction.extract_jd_requirements("Overview\nRequirements: 3.5 years of Python required. Docker preferred.")
        self.assertEqual(items[0]["minimum_years"], 3.5)
        self.assertEqual([item["line"] for item in items], [2, 2])

    def test_degree_clause_is_not_applied_to_adjacent_skills(self):
        from backend import deterministic_scoring
        result = deterministic_scoring.score("Skills\nPython", "Requirements: Python. Master's degree required.",
                                             0.5, as_of_date=date(2026, 11, 13))
        self.assertEqual(result["must_have_criterion_count"], 2)
        self.assertEqual(result["missing_must_have_criterion_count"], 1)

    def test_unknown_skill_candidates_only_come_from_prioritized_jd_lines(self):
        text = ("Summary\nAcmeCloud is our office system\nRequirements\n"
                "Experience with the AcmeCloud platform and QuantumDB database required")
        self.assertEqual(extraction.unknown_skill_candidates(text), ["AcmeCloud", "QuantumDB"])

    def test_unknown_company_names_and_business_acronyms_are_not_scoreable_skills(self):
        text = ("Requirements\nExperience with MeeCare and www.meesho required\n"
                "Must have BNPL, BRE, DPD, EAD, ECL, GNPA, LGD, NPA, RCA, and KRs")
        self.assertEqual(extraction.unknown_skill_candidates(text), [])

    def test_jobgether_boilerplate_and_generic_words_are_not_requirements(self):
        text = ("Strong programming skills in Python, with solid experience in TypeScript and Node.js.\n"
                "Hands-on experience with AI coding agents and development tools such as Claude Code, Cursor, or Codex.\n"
                "Familiarity with the Model Context Protocol (MCP) or similar tool-calling interfaces.\n"
                "Experience publishing and maintaining software packages through PyPI and npm, including version management, CI/CD pipelines, testing, and release processes.\n"
                "Experience with AWS or Google Cloud data services, including S3, Cloud Storage, and BigQuery, or with Snowflake.\n"
                "Previous experience maintaining open-source projects or working in Developer Relations.\n"
                "Benefits:\nHow Jobgether works:\nWhy Apply Through Jobgether?\n"
                "Applications are handled by Jobgether.")
        sections = extraction.split_sections(text)
        self.assertEqual(sections[-1]["name"], "non_requirements")
        unknown = {skill for item in extraction.extract_jd_requirements(text)
                   for skill in item["unknown_skills"]}
        self.assertTrue({"PyPI", "BigQuery", "Snowflake"}.issubset(unknown))
        self.assertTrue({"Code", "Context", "Hands", "How", "Jobgether", "Protocol",
                         "Relations", "Storage", "Why"}.isdisjoint(unknown))

    def test_what_sets_you_apart_requirements_exclude_xsolla_legal_and_footer_text(self):
        text = ("WHAT SETS YOU APART\n"
                "Helm.\n"
                "Experience building scalable, secure Login-as-a-Service platforms.\n"
                "Strong grasp of social login, SSO, and federated identity.\n"
                "Designed multi-tenant identity systems with isolation and configurability.\n"
                "Delivered developer-friendly APIs, SDKs, and integration documentation.\n"
                "Strong English communicator, for both technical and non-technical topics.\n"
                "The duties and responsibilities of this position may evolve over time to support the organization’s goals and individual growth.\n"
                "By submitting your application, you consent to Xsolla conducting background checks, where permitted by law, after the final interview stage.\n"
                "Xsolla takes your privacy seriously and will not sell or externally distribute any personal data received during the hiring process.\n"
                "In accordance with applicable data protection laws, Xsolla is committed to protecting your personal information and respecting your privacy.\n"
                "For any inquiries related to data privacy, please contact: careers@xsolla.com\n"
                "Explore more opportunities at: https://xsolla.com/careers")
        sections = extraction.split_sections(text)
        self.assertEqual(sections[0]["name"], "requirements")
        requirements = extraction.extract_jd_requirements(text)
        self.assertTrue(any("Helm" in item["text"] for item in requirements))
        self.assertTrue(any("Helm" in item["skills"] for item in requirements))
        self.assertFalse(any("privacy" in item["text"].casefold() or
                             "background checks" in item["text"].casefold() or
                             "duties and responsibilities" in item["text"].casefold() or
                             "explore more opportunities" in item["text"].casefold()
                             for item in requirements))

    def test_generic_application_language_does_not_make_domain_acronyms_technical_skills(self):
        text = "Requirements\nApply knowledge of PD, EAD, and LGD models to evaluate credit risk required"
        requirement, = extraction.extract_jd_requirements(text)
        self.assertEqual(requirement["unknown_skills"], [])

    def test_sections_keep_line_numbers_and_recognize_common_headers(self):
        sections = extraction.split_sections(
            "Candidate summary\nPython developer\n\nTECHNICAL SKILLS:\nPython, SQL\n"
            "Professional Experience\nBuilt Python services\nEducation\nB.Tech Computer Science"
        )
        self.assertEqual([part["name"] for part in sections],
                         ["other", "skills", "experience", "education"])
        self.assertEqual(sections[1]["start_line"], 5)
        self.assertEqual(sections[2]["start_line"], 7)
        self.assertIn("Built Python services", sections[2]["text"])

    def test_skill_boundaries_and_section_aware_evidence(self):
        mentions = extraction.extract_skill_mentions(
            "Skills\nJava, JavaScript, C++, C#, React.js\nProjects\nUsed React.js and Python"
        )
        by_skill = {item["skill"]: item for item in mentions}
        self.assertEqual(set(by_skill), {"Java", "JavaScript", "C++", "C#", "React", "Python"})
        self.assertEqual(by_skill["Java"]["matched_text"], "Java")
        self.assertEqual(by_skill["React"]["section"], "projects")
        self.assertEqual(by_skill["React"]["match_type"], "alias")
        self.assertEqual(by_skill["React"]["evidence_credit"], 0.75)
        self.assertEqual(len(extraction.extract_skill_mentions("Python\nPython", all_mentions=True)), 2)

    def test_supplemental_taxonomy_aliases_and_language_separation(self):
        mentions = extraction.extract_skill_mentions(
            "Scikit learn sklearn HuggingFace Transformers CI/CD LLMs GenAI Java JavaScript"
        )
        self.assertEqual({item["skill"] for item in mentions}, {
            "Scikit-learn", "Hugging Face Transformers", "CI/CD", "Large language models",
            "Generative AI", "Java", "JavaScript",
        })

    def test_versioned_android_taxonomy_and_framework_implications(self):
        self.assertEqual(extraction.TAXONOMY_VERSION, "skills-v6-esco-1.2.0")
        mentions = extraction.expand_implied_skills(
            extraction.extract_skill_mentions("Skills\nJetpack Compose, Coroutines, Hilt, Retrofit, Room, "
                                              "Firebase, Kafka, GraphQL, Terraform"))
        names = {item["skill"] for item in mentions}
        self.assertTrue({"Android", "Kotlin", "Kotlin Coroutines", "Hilt", "Retrofit", "Room",
                         "Firebase", "Kafka", "GraphQL", "Terraform"}.issubset(names))
        self.assertEqual({item["skill"] for item in extraction.expand_implied_skills(
            extraction.extract_skill_mentions("Requirements\nFlask and Pandas required"))},
            {"Flask", "Pandas", "Python"})

    def test_helm_is_a_recognized_requirement_and_resume_skill(self):
        result = extraction.extract_jd_requirements("WHAT SETS YOU APART\nHelm.")
        self.assertEqual(result[0]["skills"], ["Helm"])
        self.assertEqual(result[0]["unknown_skills"], [])

    def test_esco_aliases_are_limited_to_reviewed_exact_english_alternates(self):
        mentions = extraction.extract_skill_mentions(
            "C Sharp Cascading Style Sheets SEQUEL client-side JavaScript "
            "server-side JavaScript Mocha")
        self.assertEqual({item["skill"] for item in mentions}, {"C#", "CSS", "SQL", "JavaScript"})

    def test_alias_candidate_queue_ignores_common_resume_noise(self):
        text = "Requirements\nStrong proficiency with Bachelor skills required"
        self.assertEqual(extraction.unknown_skill_candidates(text), [])

    def test_directional_implications_are_partial_and_do_not_join_java_pairs(self):
        explicit = extraction.extract_skill_mentions("Neural networks JavaScript")
        implied = extraction.expand_implied_skills(explicit)
        by_skill = {item["skill"]: item for item in implied}
        self.assertEqual(by_skill["Machine learning"]["match_type"], "implies")
        self.assertEqual(by_skill["Machine learning"]["evidence_credit"], 0.4)
        self.assertNotIn("Java", by_skill)
        self.assertIn(frozenset(("Java", "JavaScript")), extraction.BLOCKED_SKILL_PAIRS)

    def test_education_evidence_keeps_level_field_and_line(self):
        evidence = extraction.extract_education_evidence(
            "Education\nB.Tech in Computer Science\nBachelors\n"
        )
        self.assertEqual(evidence[0]["line"], 2)
        self.assertEqual(evidence[0]["levels"], ["bachelors"])
        self.assertEqual(set(evidence[0]["fields"]), {"computer_science", "engineering"})

    def test_education_does_not_infer_degrees_from_it_or_office_phrases(self):
        self.assertEqual(extraction.extract_education_evidence("We love it when you use MS Office."), [])
        self.assertEqual(extraction.extract_education("Post-graduate"), ["masters"])

    def test_experience_duration_in_general_copy_is_not_a_jd_requirement(self):
        requirement, = extraction.extract_jd_requirements(
            "We have 15 years of experience serving customers.")
        self.assertEqual(requirement["priority"], "unclassified")

    def test_unknown_skill_candidates_do_not_treat_ordinary_titlecase_words_as_skills(self):
        requirement, = extraction.extract_jd_requirements(
            "Requirements\nWe have 15 years of experience serving customers and leading teams.")
        self.assertEqual(requirement["unknown_skills"], [])

    def test_sept_month_is_parsed_as_september(self):
        resume = "Experience\nDeveloper | Jun 2025 - Sept 2025"
        self.assertEqual(extraction.experience_years(resume, date(2026, 11, 11)), 0.33)

    def test_ambiguous_skill_words_need_technical_context(self):
        text = ("We excel in a fast-paced team; the rest of the day, react quickly.\n"
                "Spring 2024, swift delivery, sales, and marketing.")
        self.assertEqual(extraction.extract_skill_mentions(text), [])

    def test_requirement_priority_is_explicit_and_line_level(self):
        requirements = extraction.extract_jd_requirements(
            "Requirements\nPython required, at least 3 years experience\n"
            "Preferred Skills\nReact is a plus\nResponsibilities\nBuild software"
        )
        self.assertEqual(len(requirements), 2)
        self.assertEqual(requirements[0]["priority"], "must_have")
        self.assertEqual(requirements[0]["skills"], ["Python"])
        self.assertEqual(requirements[0]["minimum_years"], 3)
        self.assertEqual(requirements[1]["priority"], "nice_to_have")
        self.assertEqual(requirements[1]["skills"], ["React"])

    def test_year_requirements_are_must_have_without_a_heading(self):
        requirement, = extraction.extract_jd_requirements("At least 3 years of Python experience")
        self.assertEqual(requirement["priority"], "must_have")
        self.assertEqual(requirement["minimum_years"], 3)

    def test_unclassified_skills_and_title_are_fallback_requirements_only_without_cues(self):
        requirements = extraction.extract_jd_requirements(
            "We are hiring a Python developer.\nSQL and Docker are part of the stack.",
            job_title="Kotlin Engineer",
        )
        self.assertEqual({skill for item in requirements if item["priority"] == "inferred_must_have"
                          for skill in item["skills"]}, {"Python", "SQL", "Docker", "Kotlin"})

    def test_explicit_requirement_cues_disable_unclassified_fallback(self):
        requirements = extraction.extract_jd_requirements(
            "Python is required.\nWe also use SQL and Docker in the stack."
        )
        self.assertEqual({item["priority"] for item in requirements}, {"must_have", "unclassified"})

    def test_explicit_education_seniority_and_years_only(self):
        text = "Senior developer with 2 years experience and B.Tech in Computer Science"
        self.assertEqual(extraction.extract_education(text), ["bachelors"])
        self.assertEqual(extraction.extract_seniority(text), ["senior"])
        self.assertEqual(extraction.explicit_years_experience(text), [2])
        self.assertEqual(extraction.explicit_years_experience("Worked from 2020 to 2024"), [])
        self.assertEqual(extraction.explicit_years_experience("3.5-8 years of relevant work"), [3.5])

    def test_experience_date_ranges_merge_overlap_and_track_skills(self):
        resume = (
            "Experience\nPython engineer | Jan 2020 - Dec 2021\n"
            "Python and SQL developer | Jan 2021 - Dec 2022\n"
            "Projects\nPython developer | Jan 2015 - Dec 2019"
        )
        as_of = date(2024, 6, 1)
        self.assertEqual(extraction.experience_years(resume, as_of), 3.0)
        self.assertEqual(extraction.skill_experience_years(resume, as_of), {"Python": 3.0, "SQL": 2.0})

    def test_current_experience_date_uses_explicit_scoring_date(self):
        resume = "Work Experience\nPython Intern | Jun 2022 - Present"
        self.assertEqual(extraction.experience_years(resume, date(2024, 5, 1)), 2.0)
        with self.assertRaises(TypeError):
            extraction.experience_years(resume, "2024-05-01")

    def test_invalid_or_oversized_documents_fail_explicitly(self):
        with self.assertRaises(TypeError):
            extraction.normalize_text(None)
        with self.assertRaises(ValueError):
            extraction.split_sections("x" * 250001)
        with self.assertRaises(TypeError):
            extraction.extract_seniority(None)


if __name__ == "__main__":
    unittest.main()
