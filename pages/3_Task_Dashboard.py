import streamlit as st
import pandas as pd
from datetime import timedelta
from dateutil import parser as date_parser  # Added for robust parsing

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
st.title("✅ Task Dashboard")
st.link_button("🚀 Open Quietude OS Google Sheet", "https://docs.google.com/spreadsheets/d/1o5LmRv4MUQmO84bouTiBdqFzZu-lqx8V_YDVSBSsi2c/edit?gid=0#gid=0")
st.markdown("View and manage all of your tasks in one place.")

# --- AUTHENTICATION & DATA FETCHING ---
gspread_client, _, _ = authenticate_google()

@st.cache_data
def get_dashboard_data():
    """Fetches and caches the initial data from Google Sheets."""
    tasks_df = fetch_sheet_data(gspread_client, TASKS_SHEET_NAME)
    users_df = fetch_sheet_data(gspread_client, USERS_SHEET_NAME)
    
    if not tasks_df.empty and 'Due Date' in tasks_df.columns:
        # Helper function for robust date parsing
        def parse_date_safely(date_str):
            if not isinstance(date_str, str) or not date_str.strip() or date_str.lower() == 'nan':
                return pd.NaT
            try:
                # dateutil handles "2026-01-27 14:30:00" and other mixed formats automatically
                return date_parser.parse(date_str)
            except Exception:
                return pd.NaT

        # Apply the robust parser row-by-row
        tasks_df['Due Date'] = tasks_df['Due Date'].astype(str).apply(parse_date_safely)
        
    return tasks_df, users_df

# --- STATE INITIALIZATION ---
if 'tasks_df' not in st.session_state:
    tasks_df, users_df = get_dashboard_data()
    st.session_state.tasks_df = tasks_df
    st.session_state.users_df = users_df

if 'users_df' not in st.session_state:
    tasks_df, users_df = get_dashboard_data()
    st.session_state.tasks_df = tasks_df
    st.session_state.users_df = users_df

tasks_df = st.session_state.tasks_df
users_df = st.session_state.users_df
user_list = users_df['Users'].tolist() if not users_df.empty and 'Users' in users_df.columns else []


# --- UI DISPLAY ---
if st.button("🔄 Refresh Data"):
    st.cache_data.clear()
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

                # Display each task
                for _, task in status_df.iterrows():
                    task_id = task['TaskID']
                    
                    # LOGIC: Display format
                    due_date_str = "No Date"
                    if pd.notna(task['Due Date']):
                        due_date_str = task['Due Date'].strftime('%b %d, %Y')

                    # Use st.container with border instead of expander for immediate visibility
                    with st.container():
                        main_col, action_col = st.columns([3, 1])

                        # --- Main column with task details ---
                        with main_col:
                            st.subheader(f"{task['Task Name']}")
                            st.caption(f"Client: {task.get('Client', 'N/A')} | Assignee: {task.get('Assignee', 'Unassigned')}")
                            
                            # Display Key Metadata
                            st.write(f"📅 **Due:** {due_date_str}")
                            if task.get('Start Date'):
                                st.write(f"🚀 **Start:** {task['Start Date']}")
                            
                            # Notes Section
                            notes = task.get('Notes', '')
                            if notes:
                                st.info(f"📝 {notes.replace(chr(10), '  \n')}")

                        # --- Action column with buttons ---
                        with action_col:
                            st.write("**Actions**")
                            
                            if st.button("✅ Complete", key=f"complete_{task_id}", use_container_width=True, type="primary"):
                                update_task_status(gspread_client, task_id, "Done")
                                st.session_state.tasks_df.loc[st.session_state.tasks_df['TaskID'] == task_id, 'Status'] = 'Done'
                                st.rerun()

                            if status == "To Do":
                                if st.button("⏳ Waiting", key=f"waiting_{task_id}", use_container_width=True):
                                    set_task_waiting(gspread_client, task_id)
                                    st.session_state.tasks_df.loc[st.session_state.tasks_df['TaskID'] == task_id, 'Status'] = 'Waiting for Client'
                                    st.rerun()

                            if st.button("💤 Snooze", key=f"snooze_{task_id}", use_container_width=True):
                                st.session_state[f'show_task_snooze_{task_id}'] = not st.session_state.get(f'show_task_snooze_{task_id}', False)
                                st.rerun()

                            if st.button("👤 Reassign", key=f"reassign_{task_id}", use_container_width=True):
                                st.session_state[f'show_reassign_{task_id}'] = not st.session_state.get(f'show_reassign_{task_id}', False)
                                st.rerun()

                            if st.button("📝 Note", key=f"add_note_{task_id}", use_container_width=True):
                                st.session_state[f'show_add_note_{task_id}'] = not st.session_state.get(f'show_add_note_{task_id}', False)
                                st.rerun()
                                
                        # --- INLINE ACTION FORMS ---
                        
                        if st.session_state.get(f'show_task_snooze_{task_id}', False):
                            st.markdown("---")
                            st.write("**Snooze for:**")
                            c1, c2, c3 = st.columns(3)
                            if c1.button("1 Day", key=f"snz_1d_{task_id}"):
                                snooze_task(gspread_client, task_id, timedelta(days=1))
                                st.session_state.pop('tasks_df', None)
                                st.rerun()
                            if c2.button("2 Days", key=f"snz_2d_{task_id}"):
                                snooze_task(gspread_client, task_id, timedelta(days=2))
                                st.session_state.pop('tasks_df', None)
                                st.rerun()
                            if c3.button("1 Week", key=f"snz_1wk_{task_id}"):
                                snooze_task(gspread_client, task_id, timedelta(weeks=1))
                                st.session_state.pop('tasks_df', None)
                                st.rerun()

                        if st.session_state.get(f'show_reassign_{task_id}', False):
                            st.markdown("---")
                            new_assignee = st.selectbox("New Assignee", options=user_list, key=f"sel_assign_{task_id}")
                            if st.button("Confirm Reassign", key=f"conf_reassign_{task_id}"):
                                reassign_task(gspread_client, task_id, new_assignee)
                                st.session_state.pop('tasks_df', None)
                                st.rerun()

                        if st.session_state.get(f'show_add_note_{task_id}', False):
                            st.markdown("---")
                            new_note = st.text_area("Note content", key=f"txt_note_{task_id}")
                            if st.button("Save Note", key=f"save_note_{task_id}"):
                                add_note_to_task(gspread_client, task_id, new_note)
                                st.session_state.pop('tasks_df', None)
                                st.rerun()