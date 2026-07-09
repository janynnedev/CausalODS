from dataclasses import dataclass, field, asdict
from datetime import datetime



@dataclass
class Incident:


    incident_id: str


    timestamp: datetime


    root_cause_service: str


    confidence: float


    evidence: dict = field(
        default_factory=dict
    )


    status: str = "OPEN"



    def to_dict(self):

        data = asdict(self)

        data["timestamp"] = (
            self.timestamp.isoformat()
        )

        return data