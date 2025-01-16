import streamlit as st
import pandas as pd
import requests
import re
from datetime import datetime

# Constants
GITHUB_API_URL = "https://api.github.com"
WORKFLOW_RUNS_ENDPOINT = "/repos/{owner}/{repo}/actions/runs"
JOBS_ENDPOINT = "/repos/{owner}/{repo}/actions/runs/{run_id}/jobs"
LOGS_ENDPOINT = "/repos/{owner}/{repo}/actions/jobs/{job_id}/logs"
DEPLOYMENTS_ENDPOINT = "/repos/{owner}/{repo}/deployments"
PULLS_ENDPOINT = "/repos/{owner}/{repo}/pulls"  # To fetch PRs

# Utility Functions
def format_datetime(date_str):
    """Formats GitHub's datetime string to a readable format."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ")
        return dt.strftime("%B %d, %Y %I:%M %p")
    except ValueError:
        return date_str

def fetch_workflow_runs(owner, repo, token):
    """Fetches the latest workflow runs for a repository."""
    url = GITHUB_API_URL + WORKFLOW_RUNS_ENDPOINT.format(owner=owner, repo=repo)
    headers = {"Authorization": f"token {token}"}
    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        return response.json().get("workflow_runs", [])
    else:
        st.error(f"Failed to fetch workflow runs for {owner}/{repo}: {response.status_code}")
        return []

def fetch_deployments(owner, repo, token, commit_sha):
    """Fetches deployments for a commit to retrieve the deployment environment."""
    url = GITHUB_API_URL + DEPLOYMENTS_ENDPOINT.format(owner=owner, repo=repo)
    headers = {"Authorization": f"token {token}"}
    params = {"sha": commit_sha}  # Filter deployments by commit SHA
    response = requests.get(url, headers=headers, params=params)

    if response.status_code == 200:
        deployments = response.json()
        return [deployment['environment'] for deployment in deployments]
    else:
        st.warning(f"Failed to fetch deployments for {commit_sha}: {response.status_code}")
        return []

def fetch_prs(owner, repo, token):
    """Fetches open pull requests from the repository."""
    url = GITHUB_API_URL + PULLS_ENDPOINT.format(owner=owner, repo=repo)
    headers = {"Authorization": f"token {token}"}
    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        prs = response.json()
        return {pr['number']: pr['title'] for pr in prs}
    else:
        st.warning(f"Failed to fetch PRs for {owner}/{repo}: {response.status_code}")
        return {}

def fetch_job_logs(owner, repo, token, run_id):
    """Fetches logs for all jobs in a workflow run."""
    url = GITHUB_API_URL + JOBS_ENDPOINT.format(owner=owner, repo=repo, run_id=run_id)
    headers = {"Authorization": f"token {token}"}
    response = requests.get(url, headers=headers)

    if response.status_code != 200:
        st.error(f"Failed to fetch jobs for run {run_id}: {response.status_code}")
        return [], {}

    jobs = response.json().get("jobs", [])
    logs = []
    environments = {}
    for job in jobs:
        job_id = job["id"]
        logs_url = GITHUB_API_URL + LOGS_ENDPOINT.format(owner=owner, repo=repo, job_id=job_id)
        logs_response = requests.get(logs_url, headers=headers)
        if logs_response.status_code == 200:
            logs.append(logs_response.text)
        else:
            st.warning(f"Failed to fetch logs for job {job_id}: {logs_response.status_code}")
    return logs, environments

def extract_pr_number_from_logs(logs):
    """Extracts the PR_Number from logs using regex."""
    pattern = r"PR_Number=(\d+(?:\.\d+)*)"
    for log in logs:
        match = re.search(pattern, log)
        if match:
            return match.group(1)
    return None

def fetch_latest_successful_workflows(owner, repo, token):
    """Fetches the latest successful workflows grouped by environment for a repository."""
    workflow_runs = fetch_workflow_runs(owner, repo, token)
    if not workflow_runs:
        return []

    successful_runs = [
        run for run in workflow_runs if run["conclusion"] == "success"
    ]

    for run in successful_runs:
        commit_sha = run.get("head_commit", {}).get("id")
        if commit_sha:
            run["environments"] = fetch_deployments(owner, repo, token, commit_sha)
        else:
            run["environments"] = []

    runs_with_envs = []
    for run in successful_runs:
        for env in run["environments"]:
            runs_with_envs.append({**run, "environment": env})

    runs_with_envs = sorted(
        runs_with_envs, key=lambda x: x["updated_at"], reverse=True
    )
    latest_runs = {}
    for run in runs_with_envs:
        key = (repo, run["environment"])
        if key not in latest_runs:
            latest_runs[key] = run

    return list(latest_runs.values())

def process_latest_workflows(owner, repo, token, prs_data):
    """Processes the latest workflows to extract PR numbers and generate the workflow table."""
    latest_workflows = fetch_latest_successful_workflows(owner, repo, token)
    all_repos_data = []

    for workflow in latest_workflows:
        logs, _ = fetch_job_logs(owner, repo, token, workflow["id"])
        pr_number = extract_pr_number_from_logs(logs)

        all_repos_data.append({
            "Repository Name": f"{owner}/{repo}",
            "Run ID": workflow["id"],
            "Workflow Name": workflow["name"],
            "Conclusion": workflow["conclusion"],
            "Branch": workflow["head_branch"],
            "Commit Message": workflow.get("head_commit", {}).get("message", "No message"),
            "PR Number": pr_number if pr_number else "Not Found",
            "Deployment Environment": workflow["environment"],
            "Updated At": format_datetime(workflow["updated_at"]),
            "Run URL": workflow["html_url"],
        })

    return all_repos_data

def create_combined_workflow_table(all_repos_data, prs_data):
    """Creates and displays a table of combined workflows."""
    if all_repos_data:
        df = pd.DataFrame(all_repos_data)
        columns_order = [
            "Repository Name", "Run ID", "Workflow Name", "Conclusion", "Branch",
            "Commit Message", "PR Number", "Deployment Environment", "Updated At", "Run URL"
        ]
        df = df[columns_order]
        df['Updated At'] = pd.to_datetime(df['Updated At'], errors='coerce')
        latest_df = df.sort_values('Updated At').groupby(
            ['Repository Name', 'Deployment Environment'], as_index=False
        ).last()

        st.subheader("Combined Workflows (Grouped by Repository and Environment)")
        environments = latest_df.groupby("Deployment Environment")
        for env, group in environments:
            st.markdown(f"### Environment: {env}")
            for index, row in group.iterrows():
                repo_full_name = row["Repository Name"]
                pr_number = row["PR Number"]
                available_prs = prs_data.get(repo_full_name, {})
                selected_pr = st.selectbox(
                    f"Select PR for {repo_full_name} ({env})",
                    options=[None] + list(available_prs.keys()),
                    key=f"pr_{repo_full_name}_{env}_{index}"
                )
                st.write(f"Selected PR: {selected_pr} - {available_prs.get(selected_pr)}" if selected_pr else "No PR selected")
            st.dataframe(group.drop(columns=["Run URL"]))
    else:
        st.info("No successful workflows found.")

# Streamlit UI
st.title("GitHub Workflow Dashboard")
st.sidebar.header("GitHub Configuration")

repos_input = st.sidebar.text_area("Repositories (one per line)", "owner/repo\nowner/repo2")
token = st.sidebar.text_input("GitHub Token", type="password")

if st.sidebar.button("Fetch Workflow Data"):
    repos = [repo.strip() for repo in repos_input.splitlines() if repo.strip()]
    if not repos:
        st.error("Please enter at least one repository.")
    else:
        all_repos_data = []
        prs_data = {}
        with st.spinner("Fetching workflow data..."):
            for repo_full_name in repos:
                try:
                    owner, repo = repo_full_name.split("/")
                except ValueError:
                    st.error(f"Invalid repository format: {repo_full_name}. Use 'owner/repo'.")
                    continue
                prs_data[repo_full_name] = fetch_prs(owner, repo, token)
                workflows_data = process_latest_workflows(owner, repo, token, prs_data)
                all_repos_data.extend(workflows_data)
        create_combined_workflow_table(all_repos_data, prs_data)
