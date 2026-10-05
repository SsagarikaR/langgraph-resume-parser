from pydantic import BaseModel, Field

class Expereince(BaseModel):
    title: str
    company: str
    duration_months: int | None = Field(
        None, description="Duration of this role in months, computed from the dates. None if unknown."
    )
    description: str = ""

class Project(BaseModel):
    name: str
    description: str
    technologies: list[str] = Field(default_factory=list)

class Education(BaseModel):
    degree: str
    institution: str
    year: str | None = None

class ResumeProfile(BaseModel):
    name : str
    email: str | None = None
    skills: list[str] = Field(description="All technical skills, language, frameworks, database and tools")
    experience: list[Expereince] = Field(default_factory=list)
    project: list[Project] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)