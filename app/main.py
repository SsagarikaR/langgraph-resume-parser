from pathlib import Path
from app.graph.graph import graph

result= graph.invoke({
    "resume_path": "data/resumes/resume.pdf",
    "job_description": Path("data/jds/jd.txt").read_text()
})

print(result["steps"])
print(result["resume_text"][:500])