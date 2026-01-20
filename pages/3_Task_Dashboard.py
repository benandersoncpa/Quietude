import streamlit as st
import pandas as pd
from datetime import timedelta

# Import shared functions and constants from your main utility module
from quietude import (
    authenticate_google,
    fetch_sheet_data,
    update_task_status,
    set_task_waiting,
    snooze_task,
    reassign_task,
    add_note_to_task,
    TASKS_SHEET_NAME,
    USERS_SHEET_NAME
)

# --- PAGE CONFIGURATION ---
st.set_page_config(layout="wide", page_title="Task Dashboard")
st.title("✅ Task Dashboard")
st.link_button("🚀 Open Quietude OS Google Sheet", "https://docs.google.com/spreadsheets/d/1o5LmRv4MUQmO84bouTiBdqFzZu-lqx8V_YDVSBSsi2c/edit?gid=0#gid=0")
st.markdown("View and manage all of your tasks in one place.")

# --- AUTHENTICATION & DATA FETCHING ---
# Authenticate and get the client to interact with Google Sheets
gspread_client, _, _ = authenticate_google()

# Fetch all tasks and user data, caching the results to improve performance
@st.cache_data
def get_dashboard_data():
    """Fetches and caches the initial data from Google Sheets."""
    tasks_df = fetch_sheet_data(gspread_client, TASKS_SHEET_NAME)
    users_df = fetch_sheet_data(gspread_client, USERS_SHEET_NAME)
    if not tasks_df.empty and 'Due Date' in tasks_df.columns:
        tasks_df['Due Date'] = pd.to_datetime(tasks_df['Due Date'], errors='coerce')
    return tasks_df, users_df

# --- STATE INITIALIZATION ---
# Load data into session state only if it's not already there
if 'tasks_df' not in st.session_state:
    tasks_df, users_df = get_dashboard_data()
    st.session_state.tasks_df = tasks_df
    st.session_state.users_df = users_df

if 'users_df' not in st.session_state:
    tasks_df, users_df = get_dashboard_data()
    st.session_state.tasks_df = tasks_df
    st.session_state.users_df = users_df

# Use data from session state from now on
tasks_df = st.session_state.tasks_df
users_df = st.session_state.users_df
user_list = users_df['Users'].tolist() if not users_df.empty and 'Users' in users_df.columns else []


# --- UI DISPLAY ---
if st.button("🔄 Refresh Data"):
    st.cache_data.clear()
    # Clear the session state to force a refetch
    st.session_state.pop('tasks_df', None)
    st.session_state.pop('users_df', None)
    st.rerun()
if tasks_df.empty:
    st.info("No tasks found. You can create tasks from the Command Center.")
else:
    # Filter out any tasks that are already marked as "Done"
    active_tasks_df = tasks_df[tasks_df['Status'] != 'Done'].copy()

    if active_tasks_df.empty:
        st.success("✅ All tasks are completed! Nothing to show here.")
        
    else:
        # Get the unique statuses from the remaining tasks to create a tab for each one
        status_tabs = ["To Do", "Waiting for Client"]  # Define a logical order
        other_statuses = [s for s in active_tasks_df['Status'].unique() if s not in status_tabs]
        all_tabs = status_tabs + other_statuses

        tabs = st.tabs(all_tabs)

        for i, status in enumerate(all_tabs):
            with tabs[i]:
                # Filter tasks for the current tab's status
                status_df = active_tasks_df[active_tasks_df['Status'] == status].sort_values(by="Due Date", ascending=True)

                if status_df.empty:
                    st.write(f"No tasks with the status '{status}'.")
                    continue

                # Display each task as an expandable section
                for _, task in status_df.iterrows():
                    task_id = task['TaskID']
                    due_date_str = task['Due Date'].strftime('%b %d, %Y') if pd.notna(task['Due Date']) else 'N/A'
                    expander_title = f"**{task['Task Name']}** ({task.get('Client', 'N/A')}) — Due: {due_date_str}"

                    with st.expander(expander_title):
                        main_col, action_col = st.columns([3, 1])

                        # --- Main column with task details ---
                        with main_col:
                            st.markdown(f"**Client:** {task.get('Client', 'N/A')}")
                            st.markdown(f"**Due Date:** {due_date_str}")
                            st.markdown(f"**Start Date:** {task.get('Start Date', 'N/A')}")
                            st.markdown(f"**Notes:**")
                            # Display existing notes in a formatted block
                            notes = task.get('Notes', 'No notes for this task.')
                            st.markdown(f"> _{notes.replace(chr(10), '  \n')}_")

                        # --- Action column with buttons ---

                        with action_col:
                            st.write("**Actions**")
                            if st.button("Mark as Completed", key=f"complete_{task_id}", use_container_width=True, type="primary"):
                                # 1. Update the source of truth
                                update_task_status(gspread_client, task_id, "Done")
                                # 2. Update the local state to match
                                st.session_state.tasks_df.loc[st.session_state.tasks_df['TaskID'] == task_id, 'Status'] = 'Done'
                                # 3. Rerun to update the UI
                                st.rerun()

                            if status == "To Do":
                                if st.button("Waiting for Client", key=f"waiting_{task_id}", use_container_width=True):
                                    # 1. Update the source of truth
                                    set_task_waiting(gspread_client, task_id)
                                    # 2. Update the local state
                                    st.session_state.tasks_df.loc[st.session_state.tasks_df['TaskID'] == task_id, 'Status'] = 'Waiting for Client'
                                    # 3. Rerun to update the UI
                                    st.rerun()

                            # You can apply the same logic to your Snooze, Reassign, and Add Note buttons
                            # For now, we remove the cache clearing from them to prevent quota errors
                            if st.button("Snooze", key=f"snooze_{task_id}", use_container_width=True):
                                st.session_state[f'show_task_snooze_{task_id}'] = not st.session_state.get(f'show_task_snooze_{task_id}', False)
                                # No API call, just rerun
                                st.rerun()

                            if st.button("Reassign", key=f"reassign_{task_id}", use_container_width=True):
                                st.session_state[f'show_reassign_{task_id}'] = not st.session_state.get(f'show_reassign_{task_id}', False)
                                # No API call, just rerun
                                st.rerun()

                            if st.button("Add Note", key=f"add_note_{task_id}", use_container_width=True):
                                st.session_state[f'show_add_note_{task_id}'] = not st.session_state.get(f'show_add_note_{task_id}', False)
                                # No API call, just rerun
                                st.rerun()