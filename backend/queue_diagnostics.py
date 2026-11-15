"""Read-only explanations using the same gates as hosted scoring."""

import json

from backend import matching_tasks, pipeline_skeleton as pipeline, role_titles


def exclusion(conn, user, job, profile):
    if not user['active'] or not user['email_verified'] or not user['resume_text'].strip():
        return 'Account inactive, unverified, or missing resume'
    if not job['is_active']:
        return 'Job inactive'
    if not job['company_active']:
        return 'Company inactive'
    if job['company_id'] not in matching_tasks.companies_for_user(conn, user['id']):
        return 'Company not followed'
    effective = role_titles.filtering_user(user, profile)
    preferences = json.loads(effective['preferences_json'])
    selected = {}
    for key, reason in (('employment_types', 'Employment type excluded'),
                        ('roles', 'Selected role mismatch'),
                        ('locations', 'Location mismatch'),
                        ('min_stipend', 'Below minimum stipend')):
        if pipeline.is_senior_title(job['title']):
            return 'Senior or management title'
        if key in preferences:
            selected[key] = preferences[key]
        if not pipeline.filter_jobs_for_user([job], dict(user, preferences_json=json.dumps(selected))):
            return reason
    if profile is None:
        return 'Role profile required'
    if profile['status'] != 'ready':
        return 'Insufficient role-profile evidence'
    if role_titles.match_index(job['title'], profile) is None:
        return 'Role-family alias mismatch'
    return 'Eligible for rescoring'


def stale_page(conn, limit, offset):
    rows = conn.execute('''SELECT s.user_id,j.*,c.active company_active,c.name company_name
        FROM scores s JOIN jobs j ON j.id=s.job_id JOIN companies c ON c.id=j.company_id
        WHERE s.is_stale=1 ORDER BY s.user_id,j.id LIMIT ? OFFSET ?''', (limit, offset)).fetchall()
    result = []
    for job in rows:
        user = conn.execute('''SELECT u.*,COALESCE(a.email_verified,0) email_verified
            FROM users u LEFT JOIN accounts a ON a.user_id=u.id WHERE u.id=?''', (job['user_id'],)).fetchone()
        profile = role_titles.current(conn, user)
        result.append(dict(user_id=user['id'], job_id=job['id'], title=job['title'],
                           company=job['company_name'], reason=exclusion(conn, user, job, profile)))
    return result
