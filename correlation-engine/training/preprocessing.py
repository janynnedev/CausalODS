from sklearn.preprocessing import LabelEncoder



class Preprocessor:


    def __init__(self):

        self.encoder = LabelEncoder()



    def prepare(
        self,
        dataframe
    ):


        df = dataframe.copy()


        df["service_name"] = (

            self.encoder.fit_transform(

                df["service_name"]

            )

        )


        X = df.drop(
            "root_cause",
            axis=1
        )


        y = df["root_cause"]


        return X,y