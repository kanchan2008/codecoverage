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
    """
    Formats GitHub's datetime string to a readable format.
    """
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ")
        return dt.strftime("%B %d, %Y %I:%M %p")
    except ValueError:
        return date_str

def fetch_workflow_runs(owner, repo, token):
    """
    Fetches the latest workflow runs for a repository.
    """
    url = GITHUB_API_URL + WORKFLOW_RUNS_ENDPOINT.format(owner=owner, repo=repo)
    headers = {"Authorization": f"token {token}"}
    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        return response.json().get("workflow_runs", [])
    else:
        st.error(f"Failed to fetch workflow runs for {owner}/{repo}: {response.status_code}")
        return []

def fetch_deployments(owner, repo, token, commit_sha):
    """
    Fetches deployments for a commit to retrieve the deployment environment.
    """
    url = GITHUB_API_URL + DEPLOYMENTS_ENDPOINT.format(owner=owner, repo=repo)
    headers = {"Authorization": f"token {token}"}
    params = {"sha": commit_sha}  # Filter deployments by commit SHA
    response = requests.get(url, headers=headers, params=params)

    if response.status_code == 200:
        deployments = response.json()
        environments = [deployment['environment'] for deployment in deployments]
        return environments
    else:
        st.warning(f"Failed to fetch deployments for {commit_sha}: {response.status_code}")
        return []

def fetch_prs(owner, repo, token):
    """
    Fetches open pull requests from the repository.
    """
    url = GITHUB_API_URL + PULLS_ENDPOINT.format(owner=owner, repo=repo)
    headers = {"Authorization": f"token {token}"}
    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        prs = response.json()
        pr_numbers = {pr['number']: pr['title'] for pr in prs}
        return pr_numbers
    else:
        st.warning(f"Failed to fetch PRs for {owner}/{repo}: {response.status_code}")
        return {}

def fetch_job_logs(owner, repo, token, run_id):
    """
    Fetches logs for all jobs in a workflow run.
    """
    url = GITHUB_API_URL + JOBS_ENDPOINT.format(owner=owner, repo=repo, run_id=run_id)
    headers = {"Authorization": f"token {token}"}
    response = requests.get(url, headers=headers)

    if response.status_code != 200:
        st.error(f"Failed to fetch jobs for run {run_id}: {response.status_code}")
        return [], None

    jobs = response.json().get("jobs", [])
    logs = []
    environments = {}  # Initialize as an empty dictionary
    for job in jobs:
        job_id = job["id"]
        logs_url = GITHUB_API_URL + LOGS_ENDPOINT.format(owner=owner, repo=repo, job_id=job_id)
        logs_response = requests.get(logs_url, headers=headers)
        if logs_response.status_code == 200:
            logs.append(logs_response.text)
            # Fetch commit SHA and deployments for environment
            commit_sha = job.get("head_sha")
            if commit_sha:
                environments[commit_sha] = fetch_deployments(owner, repo, token, commit_sha)
        else:
            st.warning(f"Failed to fetch logs for job {job_id}: {logs_response.status_code}")
    return logs, environments

def extract_pr_number_from_logs(logs):
    """
    Extracts the PR_Number from logs using regex.
    """
    pattern = r"PR_Number=(\d+(?:\.\d+)*)"
    for log in logs:
        match = re.search(pattern, log)
        if match:
            return match.group(1)
    return None

def create_combined_workflow_table(all_repos_data, prs_data):
    """
    Combines workflow data from all repositories and displays only the latest
    'Updated At' timestamp for each unique combination of 'Repository Name' 
    and 'Deployment Environment', with a dropdown for PR selection.
    """
    if all_repos_data:
        # Convert combined data into DataFrame
        df = pd.DataFrame(all_repos_data)
        
        # Reorder columns to have "Repository Name" as the first column
        columns_order = [
            "Repository Name", "Run ID", "Workflow Name", "Conclusion", "Branch", 
            "Commit Message", "PR Number", "Deployment Environment", "Updated At", "Run URL"
        ]
        df = df[columns_order]
        
        # Convert 'Updated At' to datetime for comparison
        df['Updated At'] = pd.to_datetime(df['Updated At'], errors='coerce')

        # Group by 'Repository Name' and 'Deployment Environment', and get the latest 'Updated At'
        latest_df = df.sort_values('Updated At').groupby(
            ['Repository Name', 'Deployment Environment'], as_index=False
        ).last()

        # Display workflows grouped by environment
        st.subheader(f"Combined Workflows (Grouped by Repository and Environment)")
        environments = latest_df.groupby("Deployment Environment")
        
        for env, group in environments:
            st.markdown(f"### Environment: {env}")
            # Add PR selection dropdown for each row
            for index, row in group.iterrows():
                repo_full_name = row["Repository Name"]
                pr_number = row["PR Number"]
                available_prs = prs_data.get(repo_full_name, {})
                selected_pr = st.selectbox(f"Select PR for {repo_full_name} ({env})", 
                                          options=[None] + list(available_prs.keys()), 
                                          key=f"pr_{repo_full_name}_{env}_{index}")
                st.write(f"Selected PR: {selected_pr} - {available_prs.get(selected_pr)}" if selected_pr else "No PR selected")
            
            st.dataframe(group.drop(columns=["Run URL"]))  # Remove "Run URL" from displayed table
    else:
        st.info("No successful workflows found.")

# Streamlit UI
st.title("GitHub Workflow DashBoard")
st.sidebar.header("GitHub Configuration")

# User Inputs
repos_input = st.sidebar.text_area("Repositories (one per line)", "kanchan2008/codecoverage\nkanchan2008/testcoverage")
token = st.sidebar.text_input("GitHub Token", type="password")

if st.sidebar.button("Fetch Workflow Data"):
    repos = [repo.strip() for repo in repos_input.splitlines() if repo.strip()]
    
    if not repos:
        st.error("Please enter at least one repository.")
    else:
        all_repos_data = []  # List to collect data from all repositories
        prs_data = {}  # Dictionary to store PRs for each repository
        
        with st.spinner("Fetching workflow data..."):
            for repo_full_name in repos:
                try:
                    owner, repo = repo_full_name.split("/")
                except ValueError:
                    st.error(f"Invalid repository format: {repo_full_name}. Use 'owner/repo'.")
                    continue

                workflow_runs = fetch_workflow_runs(owner, repo, token)
                if workflow_runs:
                    prs_data[repo_full_name] = fetch_prs(owner, repo, token)  # Fetch PRs for the repo
                    for run in workflow_runs:
                        if run["conclusion"] == "success":  # Only consider successful runs
                            workflow_name = run["name"]
                            commit_sha = run.get("head_commit", {}).get("id")
                            if commit_sha:
                                logs, environments = fetch_job_logs(owner, repo, token, run["id"])
                                pr_number = extract_pr_number_from_logs(logs)
                                # Ensure we handle None values gracefully
                                environments_list = environments.get(commit_sha, []) if environments else []
                                for env in environments_list:
                                    all_repos_data.append({
                                        "Repository Name": repo_full_name,  # Move "Repository Name" to the first column
                                        "Run ID": run["id"],
                                        "Workflow Name": workflow_name,
                                        "Conclusion": run["conclusion"],
                                        "Branch": run["head_branch"],
                                        "Commit Message": run.get("head_commit", {}).get("message", "No message"),
                                        "PR Number": pr_number if pr_number else "Not Found",
                                        "Deployment Environment": env,
                                        "Updated At": format_datetime(run["updated_at"]),
                                        "Run URL": run["html_url"]
                                    })
                    
                else:
                    st.info(f"No workflow runs found for {repo_full_name}.")
        
        # Create and display the combined workflow table
        create_combined_workflow_table(all_repos_data, prs_data)
