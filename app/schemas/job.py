from pydantic import BaseModel, Field


class JobRequirements(BaseModel):
    job_title: str
    required_skills: list[str] = Field(
        description="Must-have technical skills only, one short canonical name per entry (e.g. 'AWS', 'pytest'). "
        "Keep alternatives as one entry joined with ' or ', e.g. 'Kafka or RabbitMQ' (not 'Kafka/RabbitMQ')."
    )
    preferred_skills: list[str] = Field(default_factory=list, description="Nice-to-have skills")
    required_experience_years: float | None = Field(
        None, description="Minimum years of experience required, e.g. 5 for '5+ years'. None if not stated."
    )
