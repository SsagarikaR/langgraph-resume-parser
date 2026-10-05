from pydantic import BaseModel, Field

class JobRequirements(BaseModel):
    job_title: str
    required_skills: list[str] = Field(description="Must-have technical skills only")
    preferred_skills: list[str] = Field(default_factory=list, description="Nice-to-have skills")
    