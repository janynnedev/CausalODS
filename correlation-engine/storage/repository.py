from abc import ABC, abstractmethod



class IncidentRepository(ABC):


    @abstractmethod
    def save(
        self,
        incident
    ):
        pass



    @abstractmethod
    def find_by_id(
        self,
        incident_id
    ):
        pass



    @abstractmethod
    def find_all(
        self
    ):
        pass