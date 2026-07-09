import pandas as pd



class DataLoader:


    def load(
        self,
        path
    ):

        return pd.read_csv(path)