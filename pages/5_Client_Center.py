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
st.set_page_config(layout="wide", page_title="Client Center")
st.title("👥 Client Center")
st.link_button("🚀 Open Quietude OS Google Sheet", "https://docs.google.com/spreadsheets/d/1o5LmRv4MUQmO84bouTiBdqFzZu-lqx8V_YDVSBSsi2c/edit?gid=0#gid=0")
st.markdown("Tasks grouped by Client.")

# --- AUTHENTICATION & DATA FETCHING ---
gspread_client, _, _ = authenticate_google()

@st.cache_data
def get_dashboard_data():
    """Fetches and caches the initial data from Google Sheets."""
    tasks_df = fetch_sheet_data(gspread_client, TASKS_SHEET_NAME)
    users_df = fetch_sheet_data(gspread_client, USERS_SHEET_NAME)
    if not tasks_df.empty and 'Due Date' in tasks_df.columns:
        tasks_df['Due Date'] = pd.to_datetime(tasks_df['Due Date'], errors='coerce')
    
    # Handle missing or empty client names
    if 'Client' in tasks_df.columns:
        tasks_df['Client'] = tasks_df['Client'].fillna('Unassigned').replace('', 'Unassigned')
    else:
        tasks_df['Client'] = 'Unassigned'
        
    return tasks_df, users_df

# --- STATE INITIALIZATION ---
if 'tasks_df' not in st.session_state:
    tasks_df, users_df = get_dashboard_data()
    st.session_state.tasks_df = tasks_df
    st.session_state.users_df = users_df

# Use session state data
tasks_df = st.session_state.tasks_df
users_df = st.session_state.users_df
user_list = users_df['Users'].tolist() if not users_df.empty and 'Users' in users_df.columns else []

# --- REFRESH BUTTON ---
if st.button("🔄 Refresh Data"):
    st.cache_data.clear()
    st.session_state.pop('tasks_df', None)
    st.session_state.pop('users_df', None)
    st.rerun()

# --- MAIN LOGIC ---
if tasks_df.empty:
    st.info("No tasks found.")
else:
    # 1. Get unique clients and sort them
    unique_clients = sorted(tasks_df['Client'].unique())

    # Check if there are ANY active tasks at all before looping
    global_active_check = tasks_df[~tasks_df['Status'].isin(['Done', 'Archived'])]
    if global_active_check.empty:
        st.success("✅ All active tasks for all clients are complete!")
    else:
        # 2. Iterate through each client
        for client in unique_clients:
            # Filter tasks for this specific client (includes Done/Archived)
            client_tasks = tasks_df[tasks_df['Client'] == client]
            
            # Identify ACTIVE tasks for this client (to check if we should show the client at all)
            active_client_tasks = client_tasks[~client_tasks['Status'].isin(['Done', 'Archived'])]
            open_count = len(active_client_tasks)
            
            # --- FILTER LOGIC: Skip clients with 0 active tasks ---
            if open_count == 0:
                continue

            # Expandable Group
            with st.expander(f"**{client}** ({open_count} active tasks)"):
                
                # --- TAB GENERATION LOGIC ---
                # Define the priority tabs
                status_tabs = ["To Do", "Waiting for Client"]
                
                # Find any other status present in the ACTIVE tasks for this client
                other_statuses = [s for s in active_client_tasks['Status'].unique() if s not in status_tabs]
                
                # Combine them
                all_tabs = status_tabs + other_statuses
                
                # Check if there are any "Done" tasks to decide if we need a "Done" tab
                done_exists = not client_tasks[client_tasks['Status'] == 'Done'].empty
                if done_exists:
                    all_tabs.append("Done")
                    
                # Create the tabs
                tabs = st.tabs(all_tabs)

                # Iterate through the generated tabs
                for i, status in enumerate(all_tabs):
                    with tabs[i]:
                        # Filter tasks for the current tab's status
                        status_df = client_tasks[client_tasks['Status'] == status].sort_values(by="Due Date", ascending=True)

                        if status_df.empty:
                            st.caption(f"No tasks with status: {status}")
                            continue
                        
                        # Render the tasks in this tab
                        for _, task in status_df.iterrows():
                            task_id = task['TaskID']
                            due_date_str = task['Due Date'].strftime('%b %d') if pd.notna(task['Due Date']) else 'No Date'
                            
                            # Use a container for the task row
                            with st.container(border=True):
                                col_details, col_actions = st.columns([4, 2])
                                
                                with col_details:
                                    st.markdown(f"**{task['Task Name']}**")
                                    st.caption(f"Due: {due_date_str} | Assignee: {task.get('Assignee', 'Unassigned')}")
                                    if task.get('Notes'):
                                        st.info(f"📝 {task['Notes']}")

                                with col_actions:
                                    # Don't show action buttons for 'Done' tasks
                                    if status != 'Done':
                                        c1, c2, c3 = st.columns(3)
                                        
                                        # Complete Button
                                        if c1.button("✅", key=f"comp_{task_id}", help="Mark Complete"):
                                            update_task_status(gspread_client, task_id, "Done")
                                            # Manual state update for instant feedback
                                            st.session_state.tasks_df.loc[st.session_state.tasks_df['TaskID'] == task_id, 'Status'] = 'Done'
                                            st.rerun()
                                            
                                        # Waiting Button
                                        if c2.button("⏳", key=f"wait_{task_id}", help="Set Waiting for Client"):
                                            set_task_waiting(gspread_client, task_id)
                                            # Manual state update for instant feedback
                                            st.session_state.tasks_df.loc[st.session_state.tasks_df['TaskID'] == task_id, 'Status'] = 'Waiting for Client'
                                            st.rerun()
                                            
                                        # Popover for extra actions
                                        with c3.popover("⚙️"):
                                            st.write("More Actions")
                                            
                                            # Snooze
                                            if st.button("Snooze 1 Day", key=f"snooze_{task_id}"):
                                                snooze_task(gspread_client, task_id, timedelta(days=1))
                                                # Clear session state to force re-fetch of new date
                                                st.session_state.pop('tasks_df', None)
                                                st.rerun()
                                            
                                            # Reassign
                                            new_assignee = st.selectbox("Reassign", options=user_list, key=f"reassign_box_{task_id}")
                                            if st.button("Save Assignee", key=f"save_reassign_{task_id}"):
                                                reassign_task(gspread_client, task_id, new_assignee)
                                                # Clear session state to force re-fetch
                                                st.session_state.pop('tasks_df', None)
                                                st.rerun()

                                            # Add Note
                                            note_text = st.text_input("Add Note", key=f"note_in_{task_id}")
                                            if st.button("Save Note", key=f"save_note_{task_id}"):
                                                add_note_to_task(gspread_client, task_id, note_text)
                                                # Clear session state to force re-fetch (to see timestamped note)
                                                st.session_state.pop('tasks_df', None)
                                                st.rerun()
                                    else:
                                        st.write("✅ Completed")