from dataclasses import dataclass



@dataclass
class RCAEvidence:


    service_name:str


    xgboost_score:float = 0.0


    shap_score:float = 0.0


    temporal_score:float = 0.0


    graph_score:float = 0.0


    final_score:float = 0.0