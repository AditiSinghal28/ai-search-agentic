from __future__ import annotations

INTENT_CATALOG: dict[str, dict] = {
    "booking_count": {
        "tables": ["bookings"],
        "answer_style": "single_value",
        "examples": [
            "how many bookings are there today",
            "count bookings today",
            "how many appointments today",
            "number of bookings today",
            "how many visits today",
            "how many bookings were there last month",
            "booking count this week",
            "count appointments for friday",
            "how many confirmed bookings are there today",
        ],
    },
    "booking_list": {
        "tables": ["bookings", "doctors"],
        "answer_style": "list",
        "examples": [
            "show me all pending bookings for today",
            "list completed bookings last month",
            "show all cancelled appointments",
            "display accepted bookings for this week",
            "show rejected bookings",
            "list no show bookings for friday",
        ],
    },
    "revenue_total": {
        "tables": ["patient_billing_entries"],
        "answer_style": "single_value",
        "examples": [
            "what is the total revenue this month",
            "what is the income this month",
            "how much money did we make this month",
            "what are the earnings this month",
            "what are the collections this month",
            "how much revenue was generated this week",
        ],
    },
    "doctor_most_appointments": {
        "tables": ["bookings", "doctors"],
        "answer_style": "comparison",
        "examples": [
            "which doctor has the most appointments this week",
            "which doctor has the most bookings this week",
            "who is the busiest doctor this week",
            "top doctor by appointments this month",
            "which doctor is handling the most patients",
        ],
    },
    "top_treatments_by_revenue": {
        "tables": ["patient_billing_entries", "treatments"],
        "answer_style": "list",
        "examples": [
            "what are the top treatments by revenue",
            "top 5 treatments by revenue",
            "which treatments earned the most money",
            "highest revenue treatments",
            "most profitable treatments",
        ],
    },
    "patient_count": {
        "tables": ["patients"],
        "answer_style": "single_value",
        "examples": [
            "how many patients registered this month",
            "new patient registrations last month",
            "count patients registered this week",
            "how many new patients do we have",
        ],
    },
    "busiest_time_slot": {
        "tables": ["bookings"],
        "answer_style": "single_value",
        "examples": [
            "which time slot is busiest on mondays",
            "what is the busiest time slot",
            "which slot has the most bookings",
            "most crowded appointment time",
            "busiest hour for bookings",
        ],
    },
    "busiest_day_part": {
        "tables": ["bookings"],
        "answer_style": "comparison",
        "examples": [
            "split bookings into morning noon and evening",
            "how many bookings are there for friday split into morning noon and evening",
            "show booking counts by morning noon evening",
            "which part of the day is busiest",
        ],
    },
    "unpaid_billing_count": {
        "tables": ["patient_billing_entries"],
        "answer_style": "single_value",
        "examples": [
            "are there any unpaid billing entries this week",
            "count unpaid billing entries",
            "how many unpaid bills are there",
            "show me unpaid billing records",
            "how much unpaid billing is pending",
        ],
    },
    "prescription_count": {
        "tables": ["prescriptions"],
        "answer_style": "single_value",
        "examples": [
            "how many prescriptions were written today",
            "count prescriptions today",
            "number of prescriptions this week",
            "how many prescription records are there today",
        ],
    },
    "medicine_stock": {
        "tables": ["medicines"],
        "answer_style": "single_value",
        "examples": [
            "what is the current stock of paracetamol",
            "check stock of paracetamol",
            "how much stock is left for paracetamol",
            "show me medicine stock for amoxicillin",
        ],
    },
    "medicine_inventory_count": {
        "tables": ["medicines"],
        "answer_style": "single_value",
        "examples": [
            "how many medicines do we have in stock",
            "how many medicines are available",
            "count medicines in inventory",
            "how many medicine items do we currently have",
            "how many stocked medicines are there",
        ],
    },
    "medicine_stock_total": {
        "tables": ["medicines"],
        "answer_style": "single_value",
        "examples": [
            "what is the total medicine stock",
            "sum of all medicine stock",
            "total stock across medicines",
            "how much stock do we have across all medicines",
        ],
    },
    "schedule_lookup": {
        "tables": ["schedules", "doctors"],
        "answer_style": "list",
        "examples": [
            "show doctor abinav schedule",
            "working hours of doctor abinav",
            "when is doctor abinav available",
            "doctor schedule for abinav",
        ],
    },
    "revenue_by_treatment": {
        "tables": ["patient_billing_entries", "treatments"],
        "answer_style": "single_value",
        "examples": [
            "revenue from dermatology",
            "revenue from derma",
            "how much revenue did dermatology generate",
            "income from jaundice treatment",
            "earnings from consultation",
            "money made from a treatment",
        ],
    },
}