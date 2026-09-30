from jobagent.answers import match_option, rule_answer
from jobagent.appliers.form_filler import clean_label

from .conftest import PROFILE


def test_eligibility_rules():
    assert rule_answer("Will you now or in the future require sponsorship for employment visa status?", "radio", PROFILE) == "No"
    assert rule_answer("Are you legally authorized to work in the United States?", "radio", PROFILE) == "Yes"
    assert rule_answer("Are you at least 18 years of age?", "select", PROFILE) == "Yes"
    assert rule_answer("Do you have an active security clearance?", "radio", PROFILE) == "No"


def test_contact_and_experience_rules():
    assert rule_answer("First name", "text", PROFILE) == "Alex"
    assert rule_answer("Mobile phone number", "tel", PROFILE) == "5125550100"
    assert rule_answer("Location (city)", "combobox", PROFILE) == "Austin, Texas"
    assert rule_answer("City", "text", PROFILE) == "Austin"
    assert rule_answer("How many years of work experience do you have with SQL?", "number", PROFILE) == "4"
    assert rule_answer("How did you hear about us?", "text", PROFILE) == "Job board"
    # Must not answer these with contact details
    assert rule_answer("Name of your most recent employer", "text", PROFILE) is None
    assert rule_answer("I agree to receive emails about this job", "checkbox", PROFILE) is None
    assert rule_answer("What is your ethnicity?", "select", PROFILE) == "Decline to self-identify"


def test_match_option():
    assert match_option("Decline to self-identify", ["Male", "Female", "I don't wish to answer"]) == "I don't wish to answer"
    assert match_option("Yes", ["Yes, I am", "No, I am not"]) == "Yes, I am"
    assert match_option("No", ["Yes", "No", "None of the above"]) == "No"
    assert match_option("3", ["0-1 years", "2-4 years", "5+ years"]) == "2-4 years"
    assert match_option("7", ["0-1 years", "2-4 years", "5+ years"]) == "5+ years"
    assert match_option("Bachelor's Degree", ["High School", "Bachelor's", "Master's"]) == "Bachelor's"
    assert match_option("Purple", ["Yes", "No"]) is None


def test_clean_label():
    assert clean_label("Email address *") == "Email address"
    assert clean_label("Phone (required)") == "Phone"
    assert clean_label("How many years? How many years?") == "How many years?"
