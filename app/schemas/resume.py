from pydantic import BaseModel, Field


class Experience(BaseModel):
    title: str
    company: str
    start_date: str | None = Field(
        None,
        description="Start month as YYYY-MM, e.g. 'Jul 2023' -> '2023-07'. Year only -> 'YYYY-01'. None if unknown.",
    )
    end_date: str | None = Field(
        None, description="End month as YYYY-MM. None if the role is current or the date is unknown."
    )
    is_current: bool = Field(False, description="True if the role is ongoing ('Present', 'Current', 'Now').")
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
    name: str
    email: str | None = None
    skills: list[str] = Field(
        description="All technical skills, languages, frameworks, databases and tools, one short canonical "
        "name per entry, e.g. 'AWS (EC2, S3)' -> 'AWS', 'EC2', 'S3'; 'unit testing (pytest)' -> 'pytest'."
    )
    experience: list[Experience] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
