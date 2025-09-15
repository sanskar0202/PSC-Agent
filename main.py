from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests
import os
from openai import OpenAI
import json
from datetime import datetime
import pytz
import pandas as pd

app = FastAPI(title="TheraWin APIs", version="1.0.0")

# -----------------------------
# Fake Data Stores
# -----------------------------
# USERS = [{"is_new_number": "FALSE", "timezone": "America/Los_Angeles", 
#           "first_session_status": "completed", "recurring_session_status": "scheduled", "recurring_session_count": 0,
#           "insurance_status": "verified", 
#           "name": "Sanskar", "email": "sanskarnanegaonkar@imbesideyou.world", "phone_number": "+918888491223"
#          }]

USERS = pd.read_csv("USERS.csv", dtype=str)

CLINIC = {"name": "TheraWin", 
          "first_session_type": "free", "first_session_username": "aryaman19", "first_session_eventTypeSlug": "30min",
          "recurring_session_type": "paid", "recurring_session_username": "aryaman19", 
          "recurring_session_eventTypeSlug": "50min",
          "insurance_submission_link": "https://therawin.health/insurance"
         }

AUDIT_FILE = "audit_log.txt"

# -----------------------------
# Config
# -----------------------------
CAL_BASE = "https://schedule.therawin.health/api/v2"

# -----------------------------
# Models
# -----------------------------
class DateRequest(BaseModel):
    message: str
    timezone: str
        
class RescheduleRequest(BaseModel):
    phone: str
    newStart: str  # ISO datetime

class CancelRequest(BaseModel):
    phone: str
       
    
# -----------------------------
# Functions
# -----------------------------
  
def log_event(action: str, user_number: str, details: str = ""):

    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    entry = f"[{timestamp}] ACTION={action} USER={user_number} DETAILS={details}\n"

    with open(AUDIT_FILE, "a") as f:
        f.write(entry)

        
# -----------------------------
# Webhooks
# -----------------------------


    
    
# -----------------------------
# Endpoints
# -----------------------------

# @app.get("/users")
# def get_user_data(phone: str):
#     """Fetch user data by phone number"""
#     for user in USERS:
#         if user["phone_number"] == phone:
#             break
#     if not user:
#         return {"is_new_number": "TRUE"}
#     return user

@app.get("/users")
def get_user_data(phone: str):

    global USERS 
    
    user = USERS.query("phone_number==@phone")
    
    if user.empty:
        user = {"is_new_number": "FALSE", "phone_number": phone, "timezone": "America/Los_Angeles", 
                "first_session_status": "not_scheduled", "insurance_status": "not_submitted",
                "recurring_session_status": "not_scheduled", "recurring_session_count": 0, "name": "", "email": ""}
        USERS = pd.concat([USERS, pd.DataFrame(user, index=[0])])
        USERS.to_csv("USERS.csv", index=False)
    else:
        user = user.iloc[0].to_dict()
    
    return user


# @app.get("/clinics/{username}")
@app.get("/clinic")
def get_clinic_data():
    return CLINIC


@app.post("/parse_date")
def parse_date(req: DateRequest):

    client = OpenAI(api_key="")

    prompt = f"""
    The current UTC datetime is {datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S %A")}.
    User input: "{req.message}"
    Convert it to date range in the future including today if required (YYYY-MM-DD) in {req.timezone} timezone, respond ONLY in JSON:
    {{"start": "...", "end": "..."}}"""
    
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You output only JSON."},
            {"role": "user", "content": prompt}
        ],
        temperature=0
    )
    return json.loads(resp.choices[0].message.content.strip())


@app.get("/first_session_variables")
def set_first_session_variables():
    return {"session_type": CLINIC["first_session_type"], "username": CLINIC["first_session_username"], 
            "eventTypeSlug": CLINIC["first_session_eventTypeSlug"]}


@app.get("/recurring_session_variables")
def set_first_session_variables():
    return {"session_type": CLINIC["recurring_session_type"], "username": CLINIC["recurring_session_username"], 
            "eventTypeSlug": CLINIC["recurring_session_eventTypeSlug"]}


@app.post("/book_appointment")
def book_appointment(name: str, email: str, phone_number: str, timezone: str, start_datetime: str, username: str, eventTypeSlug: str):
    
    global USERS
    
    HEADERS = {
        "Authorization": f"Bearer ",
        "cal-api-version": "2024-08-13",
        "Content-Type": "application/json"
    }

    API = "https://schedule.therawin.health/api/v2/bookings"

    payload = {
        "username": username,
        "eventTypeSlug": eventTypeSlug,
        "start": start_datetime,
        "attendee": {
            "name": name,
            "email": email,
            "phoneNumber": phone_number,
            "timeZone": timezone,
        }
    }
    
    response = requests.post(API, headers=HEADERS, json=payload)
    data = response.json()
    
    if data.get("status") == "success":
        user = USERS.query("phone_number==@phone_number").iloc[0]
        
        USERS.loc[USERS["phone_number"]==phone_number, "is_new_number"] = "FALSE"
        USERS.loc[USERS["phone_number"]==phone_number, "name"] = name
        USERS.loc[USERS["phone_number"]==phone_number, "email"] = email
        
        if user["first_session_status"] == "not_scheduled":
            USERS.loc[USERS["phone_number"]==phone_number, "first_session_status"] = "scheduled"
        elif user["recurring_session_status"] == "not_scheduled":
            USERS.loc[USERS["phone_number"]==phone_number, "recurring_session_status"] = "scheduled"
            
        USERS.to_csv("USERS.csv", index=False)
        
        log_event(action="BOOKED", user_number=phone_number, details=f"Booked for {start_datetime}")
    
    return data
    
    
@app.get("/latest_session")
def get_latest_session(email: str, username: str, eventTypeSlug: str):
    HEADERS = {
        "Authorization": f"Bearer ",
        "cal-api-version": "2024-08-13",
        "Content-Type": "application/json"
    }

    url = f"https://schedule.therawin.health/api/v2/bookings?status=upcoming&status=past&attendeeEmail={email}"

    response = requests.get(url, headers=HEADERS)
    data = response.json()
    
    if data.get("status") != "success":
        return data  # return error directly

    filtered = [
        booking for booking in data["data"]
        if any(h["username"] == username for h in booking.get("hosts", []))
        and booking["eventType"]["slug"] == eventTypeSlug
        and any(a["email"] == email for a in booking.get("attendees", []))
    ]

    if not filtered:
        return {"status": "not_found", "message": "No matching booking found"}

    latest = filtered[0]
    
    return latest


@app.post("/reschedule_appointment")
def reschedule_appointment(phone_number: str, booking_uid: str, start_datetime: str):
    
    HEADERS = {
        "Authorization": f"Bearer ",
        "cal-api-version": "2024-08-13",
    }

    url = f"https://schedule.therawin.health/api/v2/bookings/{booking_uid}/reschedule"
    
    response = requests.post(url, headers=HEADERS, json={"start": start_datetime})
    data = response.json()
    
    if data.get("status") == "success":
        log_event(action="RESCHEDULED", user_number=phone_number, details=f"Rescheduled to {start_datetime}")
    
    return data


@app.post("/cancel_appointment")
def cancel_appointment(phone_number: str, booking_uid: str, cancellation_reason: str):
    
    HEADERS = {
        "Authorization": f"Bearer ",
        "cal-api-version": "2024-08-13",
    }

    url = f"https://schedule.therawin.health/api/v2/bookings/{booking_uid}/cancel"
    
    response = requests.post(url, headers=HEADERS, json={"cancellationReason": cancellation_reason})
    data = response.json()
    
    if data.get("status") == "success":
        
        user = USERS.query("phone_number==@phone_number").iloc[0]
        
        if user["first_session_status"] == "scheduled":
            USERS.loc[USERS["phone_number"]==phone_number, "first_session_status"] = "not_scheduled"
        elif user["recurring_session_status"] == "scheduled":
            USERS.loc[USERS["phone_number"]==phone_number, "recurring_session_status"] = "not_scheduled"
            
        USERS.to_csv("USERS.csv", index=False)
        
        log_event(action="CANCELLED", user_number=phone_number, details=f"Cancellation Reason: {cancellation_reason}")
    
    return data