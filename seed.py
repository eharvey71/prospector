from google.cloud import firestore

UID = "ewa4WvPd5CT5SIJCmHlWAqbWaqt2"   # your auth UID from Step 1
db = firestore.Client(project="job-engine-c8f9c")

profile = {
    "name": "Eric Harvey",
    "email": "planetside0@gmail.com",
    "location": "Richmond, VA",
    "work_auth": "US citizen",
    "salary_target": None,
    "skills": ["Python", "FastAPI", "LangGraph", "LangChain", "LiteLLM", "AWS Bedrock",
           "prompt engineering", "RAG", "DeepEval", "async SQLAlchemy", "Celery",
           "RESTful APIs", "LTI", "SSO/SIS integration", "LMS platforms",
           "PostgreSQL", "MySQL", "MongoDB", "AWS", "GCP", "Firebase",
           "DevOps", "release management", "pre-sales engineering",
           "stakeholder management", "team leadership", "technical documentation"],
    "work_history": [
        {
            "company": "Unicon",
            "title": "Senior Backend Developer",
            "start": "2024-01",
            "end": None,
            "bullets": [
                "Backend developer on LearnVia, an EdTech AI platform: LangGraph agent orchestration, LiteLLM routing, and AWS Bedrock integration with prompt caching",
                "Built FastAPI services with async SQLAlchemy and Celery/Valkey task pipelines for AI feature processing",
                "Developed AI chapter-summary pipeline with cost measurement via LiteLLM spend logs and evaluation with DeepEval",
                "Delivered LMS integrations and EdTech platform engineering for a major non-profit math platform",
                "Owned DevOps and release management responsibilities across the platform",
            ],
        },
        {
            "company": "LearningClues",
            "title": "Integrations Developer / Customer Success",
            "start": "2023-01",
            "end": "2024-01",
            "bullets": [
                "Built AI solution integrations for education clients through trial and POC implementations",
                "Built a video integrations management portal enabling LLM consumption of video transcripts",
            ],
        },
        {
            "company": "Kaltura",
            "title": "Senior Solutions Engineer",
            "start": "2022-01",
            "end": "2023-01",
            "bullets": [
                "Ran pre- and post-sale demonstrations and trial implementations across Kaltura's video product stack",
                "Among the first to drive new K12 and higher-ed sales for the Kaltura Events Platform",
            ],
        },
        {
            "company": "Class Technologies",
            "title": "Director, Solutions Consulting / Solutions Engineer",
            "start": "2020-01",
            "end": "2022-01",
            "bullets": [
                "Led a team of implementation engineers deploying the Class virtual classroom (built on Zoom) across all education verticals",
                "Advised on new backend API design and deployed the company's first student rostering integration",
                "Built deployment strategies for partners and key business development opportunities",
            ],
        },
        {
            "company": "Virginia Commonwealth University",
            "title": "Senior Manager, Learning Systems / Academic Technologies",
            "start": "2017-01",
            "end": "2020-01",
            "bullets": [
                "Led a team of analysts centrally supporting Canvas, Blackboard, Kaltura, and Echo360 for the university",
                "Fostered partnerships with the Center for Teaching and Learning Excellence and VCU Online",
                "Engaged university stakeholders and school leadership on deployment strategy and best practices",
            ],
        },
        {
            "company": "Instructure",
            "title": "Senior Solutions Engineer",
            "start": "2014-01",
            "end": "2017-01",
            "bullets": [
                "Partnered with sales to bring Canvas LMS to new east-coast clients in a historically difficult adoption region",
                "Collaborated with academic technologists and instructional designers on modern LMS teaching methodologies",
                "Recognized as the subject matter expert on product accessibility",
            ],
        },
        {
            "company": "Echo360",
            "title": "Senior Solutions Engineer",
            "start": "2011-01",
            "end": "2014-01",
            "bullets": [
                "Captured some of the company's first significant cloud-hosted customers",
                "Surpassed revenue target by 106% in 2011 and 164% in 2013; won the Echo360 Sales Star Award in 2012",
            ],
        },
    ],
    "writing_samples": [
        # {"title": "Sample name", "text": "paste real writing here"},
    ],
    "preferences": {
        "titles": ["AI Engineer", "Solutions Architect", "AI Solutions Architect"],
        "remote_only": False,
        "exclude_companies": [],
        "min_match_score": 70,
    },
}

watchlist = {
    "greenhouse": ["anthropic", "gitlab", "instructure"],
    "lever": [],
}

db.collection("users").document(UID).set(profile)          # replaces the doc (bye, "rere")
db.collection("users").document(UID).collection("watchlist") \
  .document("companies").set(watchlist)

print("Seeded profile + watchlist for", UID)

UID = "ewa4WvPd5CT5SIJCmHlWAqbWaqt2"

count = 0
for doc in db.collection("users").document(UID).collection("applications").stream():
    doc.reference.delete()
    count += 1
print(f"deleted {count} applications")

count = 0
for doc in db.collection("jobPostings").stream():
    doc.reference.delete()
    count += 1
print(f"deleted {count} postings")