import os.path
import streamlit as st
import pandas as pd
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from datetime import datetime, time, timedelta, timezone
from dateutil import parser as date_parser

# --- CONFIGURATION ---
SPREADSHEET_ID = st.secrets["SPREADSHEET_ID"]
COMPLETE_LABEL_ID = st.secrets["COMPLETE_LABEL_ID"]
LABEL_ID_AEGIS_EMAIL = st.secrets["LABEL_ID_AEGIS_EMAIL"]
LABEL_ID_PERSONAL_EMAIL = st.secrets["LABEL_ID_PERSONAL_EMAIL"]
LABEL_ID_AEGIS_GV = st.secrets["LABEL_ID_AEGIS_GV"]
LABEL_ID_1099_GV = st.secrets["LABEL_ID_1099_GV"]
TASKS_SHEET_NAME = 'Tasks'
ACTIVE_WORKFLOWS_SHEET_NAME = 'Active_Workflows'
CALENDAR_ID = 'primary'

# Scheduling Parameters
WORK_START_TIME_FLOOR = time(9, 0) # The earliest the day can start
WORK_END_TIME = time(23, 0)
FOCUS_BLOCK_MINUTES = 120
BREAK_MINUTES = 15
# --- MODIFICATION: Removed Comms Block variables ---
# TOTAL_COMMS_MINUTES = 180
# NUM_COMMS_BLOCKS = 3

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive.file',
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/calendar.readonly'
]

# --- AUTHENTICATION ---
def authenticate_google():
    """Handles Google authentication for Sheets and Calendar APIs."""
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('.streamlit/credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    
    gspread_client = gspread.authorize(creds)
    calendar_service = build('calendar', 'v3', credentials=creds)
    return gspread_client, calendar_service

# --- DATA FETCHING ---
def get_tasks_and_workflows(gspread_client):
    """Fetches tasks and merges them with active workflow data."""
    try:
        tasks_worksheet = gspread_client.open_by_key(SPREADSHEET_ID).worksheet(TASKS_SHEET_NAME)
        tasks = pd.DataFrame(tasks_worksheet.get_all_records())
        
        workflows_worksheet = gspread_client.open_by_key(SPREADSHEET_ID).worksheet(ACTIVE_WORKFLOWS_SHEET_NAME)
        workflows = pd.DataFrame(workflows_worksheet.get_all_records())
        
        tasks_with_workflows = pd.merge(
            tasks, workflows[['ActiveWorkflowID', 'External Deadline']],
            how='left', on='ActiveWorkflowID'
        )
        
        tasks_todo = tasks_with_workflows[tasks_with_workflows['Status'] == 'To Do'].copy()
        for col in ['Estimated Time', 'Enjoyment', 'Importance']:
            tasks_todo[col] = pd.to_numeric(tasks_todo[col], errors='coerce').fillna(0)
        return tasks_todo
    except Exception as e:
        print(f"Error fetching data: {e}")
        return pd.DataFrame()

def get_calendar_events(calendar_service):
    """Fetches today's events from Google Calendar."""
    try:
        now = datetime.now(timezone.utc)
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = start_of_day + timedelta(days=1)
        
        events_result = calendar_service.events().list(
            calendarId=CALENDAR_ID, timeMin=start_of_day.isoformat(),
            timeMax=end_of_day.isoformat(), singleEvents=True,
            orderBy='startTime'
        ).execute()
        return events_result.get('items', [])
    except Exception as e:
        print(f"Error fetching calendar events: {e}")
        return []

# --- SCHEDULING LOGIC ---
def calculate_priority_score(task):
    """Calculates a priority score with a massive bonus for external deadlines."""
    today = datetime.now().date()
    
    external_deadline_bonus = 0
    if pd.notna(task['External Deadline']) and task['External Deadline'] != '':
        try:
            deadline = date_parser.parse(task['External Deadline']).date()
            if (deadline - today).days <= 7:
                external_deadline_bonus = 1000
        except (TypeError, ValueError):
            pass

    try:
        due_date = date_parser.parse(task['Due Date']).date()
        days_until_due = (due_date - today).days
    except (TypeError, ValueError):
        days_until_due = 99

    urgency_bonus = 0
    if days_until_due <= 1:
        urgency_bonus = 100
    elif days_until_due <= 3:
        urgency_bonus = 50
    
    importance_score = task['Importance'] * 10
    return external_deadline_bonus + urgency_bonus + importance_score

def get_available_slots(events, schedule_already_generated=False):
    """Calculates available time slots between fixed appointments."""
    today = datetime.now().date()
    local_tz = datetime.now().astimezone().tzinfo
    
    nine_am_today = datetime.combine(today, WORK_START_TIME_FLOOR, tzinfo=local_tz)
    
    # If a schedule has not been generated yet, the work start time is the current time.
    # Otherwise, we will use the previously established start time to prevent the schedule from shifting.
    if schedule_already_generated:
        work_start = nine_am_today
    else:
        current_time_local = datetime.now().astimezone(local_tz)
        work_start = max(nine_am_today, current_time_local)

    work_end = datetime.combine(today, WORK_END_TIME, tzinfo=local_tz)
    
    busy_slots = []
    for event in events:
        start = date_parser.parse(event['start'].get('dateTime', event['start'].get('date'))).astimezone(local_tz)
        end = date_parser.parse(event['end'].get('dateTime', event['end'].get('date'))).astimezone(local_tz)
        busy_slots.append((start, end))
    
    available_slots = []
    current_time = work_start
    
    for start, end in sorted(busy_slots):
        if current_time < start:
            available_slots.append((current_time, start))
        current_time = max(current_time, end)
        
    if current_time < work_end:
        available_slots.append((current_time, work_end))
        
    return available_slots

def generate_schedule(schedule_already_generated=False):
    """The main function to generate the daily plan."""
    print("Generating daily plan...")
    gspread_client, calendar_service = authenticate_google()
    
    tasks_df = get_tasks_and_workflows(gspread_client)
    events = get_calendar_events(calendar_service)
    
    if tasks_df.empty:
        print("No tasks to schedule.")
        return [], events
        
    tasks_df['priority_score'] = tasks_df.apply(calculate_priority_score, axis=1)
    tasks_df = tasks_df.sort_values(
        by=['priority_score', 'Client'], 
        ascending=[False, True]
    ).reset_index(drop=True)
    
    available_slots = get_available_slots(events, schedule_already_generated)
    
    schedule = []
    task_idx = 0
    
    # --- MODIFICATION: The entire logic for creating Comms Blocks has been commented out ---
    # comms_block_duration = timedelta(minutes=TOTAL_COMMS_MINUTES // NUM_COMMS_BLOCKS)
    
    # comms_slots_indices = []
    # if len(available_slots) > 0: comms_slots_indices.append(0)
    # if len(available_slots) > 1: comms_slots_indices.append(len(available_slots) // 2)
    # if len(available_slots) > 2: comms_slots_indices.append(len(available_slots) - 1)
    
    # temp_slots = []
    # for i, (start, end) in enumerate(available_slots):
    #     if i in comms_slots_indices:
    #         if (end - start) >= comms_block_duration:
    #             schedule.append({'title': 'Comms Block', 'start': start, 'end': start + comms_block_duration, 'type': 'comms'})
    #             if end > start + comms_block_duration:
    #                 temp_slots.append((start + comms_block_duration, end))
    #     else:
    #         temp_slots.append((start, end))
    # available_slots = temp_slots

    for start, end in available_slots:
        current_time = start
        while current_time < end:
            if end - current_time >= timedelta(minutes=FOCUS_BLOCK_MINUTES):
                focus_end = current_time + timedelta(minutes=FOCUS_BLOCK_MINUTES)
                schedule.append({'title': 'Focus Block', 'start': current_time, 'end': focus_end, 'type': 'focus'})
                focus_time_filled = timedelta(0)
                while task_idx < len(tasks_df):
                    task = tasks_df.iloc[task_idx]
                    task_duration = timedelta(minutes=int(task['Estimated Time']))
                    if focus_time_filled + task_duration <= timedelta(minutes=FOCUS_BLOCK_MINUTES):
                        task_start = current_time + focus_time_filled
                        # --- UPDATED: Add more details to the schedule item ---
                        schedule.append({
                            'title': task['Task Name'], 
                            'client': task['Client'],
                            'task_id': task['TaskID'],
                            'start': task_start, 
                            'end': task_start + task_duration, 
                            'type': 'task'
                        })
                        focus_time_filled += task_duration
                        task_idx += 1
                    else:
                        break
                
                current_time = focus_end
                if end - current_time >= timedelta(minutes=BREAK_MINUTES):
                    schedule.append({'title': 'Break', 'start': current_time, 'end': current_time + timedelta(minutes=BREAK_MINUTES), 'type': 'break'})
                    current_time += timedelta(minutes=BREAK_MINUTES)
                else:
                    break
            else:
                while task_idx < len(tasks_df):
                    task = tasks_df.iloc[task_idx]
                    task_duration = timedelta(minutes=int(task['Estimated Time']))
                    if current_time + task_duration <= end:
                        schedule.append({
                            'title': task['Task Name'], 
                            'client': task['Client'],
                            'task_id': task['TaskID'],
                            'start': current_time, 
                            'end': current_time + task_duration, 
                            'type': 'task'
                        })
                        current_time += task_duration
                        task_idx += 1
                    else:
                        break
                break

    print("Schedule generated.")
    return sorted(schedule, key=lambda x: x['start']), events
if __name__ == '__main__':
    schedule, events = generate_schedule()
    print("\n--- FIXED APPOINTMENTS ---")
    for event in events:
        start_time = date_parser.parse(event['start'].get('dateTime', event['start'].get('date'))).strftime('%H:%M')
        print(f"- {start_time}: {event['summary']}")
    print("\n--- GENERATED SCHEDULE ---")
    for item in schedule:
        client_info = f" ({item['client']})" if item.get('client') else ""
        print(f"- {item['start'].strftime('%H:%M')} - {item['end'].strftime('%H:%M')}: {item['title']}{client_info}")