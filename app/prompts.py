SCHEMA_SUMMARY = """
Use only the following database schema.

bookings:
- id, hospital_id, doctor_id, patient_name, patient_email, patient_phone, age, cause,
  booking_date, start_time, end_time,
  status enum('unverified','pending','accepted','rejected','cancelled','no_show','rescheduled','completed'),
  completed_at, reschedule_reason, rescheduled_at, approved_by, approved_at, created_at, updated_at

booking_treatments:
- id, booking_id, treatment_id, quantity, unit_price, discount_amount, total_amount, notes, created_at, updated_at

caseentries:
- id, booking_id, hospital_id, patient_id, doctor_id, complaints, examination, diagnosis, notes, created_at, updated_at

doctors:
- id, hospital_id, name, gender, specialization_id, doctor_code, experience_years, qualification,
  consultation_fee, phone, slot, created_at, updated_at

hospital_financials:
- id, hospital_id, type enum('profit','expense'), description, amount, entry_date, created_by, created_at, updated_at

investigations:
- id, case_entry_id, test_name, remarks, created_at, updated_at

medicines:
- id, hospital_id, name, unit, dosage, price, stock, description, created_at, updated_at

patients:
- id, name, phone_no, ic_passport_no, age, gender, dob, blood_type, marital_status,
  nationality, address, state, city, postcode, country, emergency_contact_name,
  emergency_contact_no, created_at, updated_at

patient_billing_entries:
- id, patient_id, hospital_id, booking_id, treatment_id,
  type enum('consultation','medicine','treatment','operation','custom_profit','custom_expense'),
  description, amount, is_past_note, is_paid, paid_at, created_at, updated_at

prescriptions:
- id, booking_id, notes, case_entry_id, medicine_name, dosage, frequency, duration,
  instructions, created_at, updated_at, patient_id, hospital_id, doctor_id

prescription_items:
- id, prescription_id, medicine_id, quantity, price_at_time, dosage_instructions, created_at, updated_at

procedures:
- id, case_entry_id, procedure_name, notes, created_at, updated_at

schedules:
- id, doctor_id, day, start_time, end_time, is_off, created_at, updated_at

specializations:
- id, hospital_id, specialization, description, created_at, updated_at

treatments:
- id, hospital_id, name, code, category enum('consultation','treatment','operation','other'),
  base_price, is_active, created_at, updated_at

users:
- id, name, email, role enum('super_admin','hospital_admin','doctor'), hospital_id,
  doctor_id, status, api_code, created_at, updated_at
"""

PLANNER_PROMPT = """
You are a medical analytics planner.
Return strict JSON only.
Prefer these intents when possible:
- booking_count
- booking_list
- revenue_total
- doctor_most_appointments
- top_treatments_by_revenue
- patient_count
- busiest_day_part
- busiest_time_slot
- unpaid_billing_count
- prescription_count
- medicine_stock
- schedule_lookup
- generic_sql

Return fields:
- intent
- date_from
- date_to
- needs_time_split
- needs_comparison
- relevant_tables
- reasoning_summary
- answer_style
- sql_notes
- entities
"""

SQL_GENERATOR_PROMPT = """
You are a careful SQL generator.
Return strict JSON only with fields: sql, parameters, chart_hint, explanation.
Rules:
- Read-only only.
- Single SELECT or WITH ... SELECT.
- Always filter hospital_id where relevant.
- Use named parameters without unsafe string interpolation.
- chart_hint may be a string or object.
"""

ANSWER_PROMPT = """
You are a medical booking analytics assistant.
Answer in concise natural language.
"""
