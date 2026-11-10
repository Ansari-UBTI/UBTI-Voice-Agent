"""Static catalogs: voices, languages, models, agent templates."""

LANGUAGES = [
    {"code": "auto", "name": "Auto-detect (multilingual)"},
    {"code": "en-IN", "name": "English (India)"}, {"code": "hi-IN", "name": "Hindi"},
    {"code": "ta-IN", "name": "Tamil"}, {"code": "te-IN", "name": "Telugu"},
    {"code": "kn-IN", "name": "Kannada"}, {"code": "ml-IN", "name": "Malayalam"},
    {"code": "mr-IN", "name": "Marathi"}, {"code": "bn-IN", "name": "Bengali"},
    {"code": "ar-AE", "name": "Arabic (UAE)"}, {"code": "en-US", "name": "English (US)"},
    {"code": "en-GB", "name": "English (UK)"},
]

MODELS = [
    {"id": "gpt-realtime", "name": "GPT Realtime", "note": "Most natural, best reasoning"},
    {"id": "gpt-realtime-mini", "name": "GPT Realtime Mini", "note": "Lowest cost & latency"},
    {"id": "gpt-4o", "name": "GPT-4o (cascaded)", "note": "Azure Speech in/out"},
    {"id": "gpt-4o-mini", "name": "GPT-4o mini (cascaded)", "note": "Budget"},
]


def _v(name, label, locale, gender, accent, style, vtype="azure-standard", hd=False):
    return {"id": name, "name": label, "locale": locale, "gender": gender, "accent": accent,
            "style": style, "type": vtype, "hd": hd, "category": "premade"}


VOICES = [
    _v("en-IN-AartiNeural", "Aarti", "en-IN", "Female", "Indian English", "Warm, friendly"),
    _v("en-IN-ArjunNeural", "Arjun", "en-IN", "Male", "Indian English", "Confident, clear"),
    _v("en-IN-NeerjaNeural", "Neerja", "en-IN", "Female", "Indian English", "Cheerful"),
    _v("en-IN-PrabhatNeural", "Prabhat", "en-IN", "Male", "Indian English", "Calm"),
    _v("hi-IN-SwaraNeural", "Swara", "hi-IN", "Female", "Hindi", "Soft, polite"),
    _v("hi-IN-MadhurNeural", "Madhur", "hi-IN", "Male", "Hindi", "Professional"),
    _v("ta-IN-PallaviNeural", "Pallavi", "ta-IN", "Female", "Tamil", "Friendly"),
    _v("ta-IN-ValluvarNeural", "Valluvar", "ta-IN", "Male", "Tamil", "Formal"),
    _v("te-IN-ShrutiNeural", "Shruti", "te-IN", "Female", "Telugu", "Bright"),
    _v("kn-IN-SapnaNeural", "Sapna", "kn-IN", "Female", "Kannada", "Warm"),
    _v("ml-IN-SobhanaNeural", "Sobhana", "ml-IN", "Female", "Malayalam", "Gentle"),
    _v("ar-AE-FatimaNeural", "Fatima", "ar-AE", "Female", "Emirati Arabic", "Elegant"),
    _v("ar-AE-HamdanNeural", "Hamdan", "ar-AE", "Male", "Emirati Arabic", "Assured"),
    _v("en-US-AvaMultilingualNeural", "Ava (Multilingual)", "en-US", "Female", "American", "Expressive, speaks 40+ languages"),
    _v("en-US-AndrewMultilingualNeural", "Andrew (Multilingual)", "en-US", "Male", "American", "Natural, speaks 40+ languages"),
    _v("en-US-EmmaMultilingualNeural", "Emma (Multilingual)", "en-US", "Female", "American", "Upbeat"),
    _v("en-US-Ava:DragonHDLatestNeural", "Ava HD", "en-US", "Female", "American", "Ultra-realistic HD", hd=True),
    _v("en-US-Andrew:DragonHDLatestNeural", "Andrew HD", "en-US", "Male", "American", "Ultra-realistic HD", hd=True),
    _v("en-GB-SoniaNeural", "Sonia", "en-GB", "Female", "British", "Polished"),
] + [
    {"id": n, "name": n.capitalize(), "locale": "multi", "gender": g, "accent": "Neutral",
     "style": "OpenAI realtime native voice", "type": "openai", "hd": True, "category": "premade"}
    for n, g in [("marin", "Female"), ("cedar", "Male"), ("alloy", "Neutral"), ("coral", "Female"),
                 ("sage", "Female"), ("shimmer", "Female"), ("ash", "Male"), ("verse", "Male"), ("echo", "Male")]
]

TEMPLATES = [
    {"id": "receptionist", "name": "Front-desk Receptionist", "icon": "PhoneIncoming",
     "first_message": "Hello! Thanks for calling. How can I help you today?",
     "system_prompt": "You are Priya, the front-desk receptionist for {company}. Answer questions about the business, "
                      "capture the caller's name and need, and offer to book a callback. Be warm and brief."},
    {"id": "real_estate", "name": "Real Estate Lead Qualifier", "icon": "Building2",
     "first_message": "Hi, this is Sara from the property team. Do you have a minute to talk about the home you enquired about?",
     "system_prompt": "You qualify property leads. Ask, one at a time: budget, preferred location, bedrooms, ready vs off-plan, "
                      "timeline to buy, and best time for a site visit. Summarise and confirm before ending."},
    {"id": "appointment", "name": "Appointment Booking", "icon": "CalendarCheck",
     "first_message": "Hello! I can help you book, change, or cancel an appointment. What would you like to do?",
     "system_prompt": "You manage appointments. Collect name, phone, preferred date and time, and reason for visit. "
                      "Repeat the details back and confirm."},
    {"id": "support", "name": "Customer Support", "icon": "Headset",
     "first_message": "Hi, you've reached customer support. What can I help you with?",
     "system_prompt": "You are a patient support agent. Understand the issue, answer from the knowledge base, and if you "
                      "cannot resolve it, collect details and promise a callback within 24 hours."},
    {"id": "collections", "name": "Payment Reminder", "icon": "Wallet",
     "first_message": "Hello {{customer_name}}, this is a quick courtesy call regarding your pending payment. Is this a good time?",
     "system_prompt": "You politely remind {{customer_name}} of a pending payment of {{due_amount}} due on {{due_date}}. "
                      "Confirm identity, share amount and due date, ask for a commitment date. Never threaten. Stay respectful."},
    {"id": "outbound_lead", "name": "Outbound Lead Follow-up", "icon": "PhoneOutgoing",
     "first_message": "Hi {{customer_name}}, this is Sara from {company}. You enquired with us recently - is now a good time?",
     "system_prompt": "You call people who enquired with {company}. The customer's name is {{customer_name}}. "
                      "Confirm their interest, answer questions briefly, and offer to book a callback or visit. "
                      "If they are busy, ask for a better time and end politely."},
    {"id": "logistics", "name": "Shipment Status Desk", "icon": "Truck",
     "first_message": "Hello! Please tell me your order or shipment number and I'll check the status.",
     "system_prompt": "You help customers track shipments, explain delays, and capture delivery change requests."},
]
