import joblib



class ModelRegistry:



    def save(

        self,

        model,

        path="models/xgboost_rca.pkl"

    ):


        joblib.dump(

            model,

            path

        )



    def load(

        self,

        path="models/xgboost_rca.pkl"

    ):


        return joblib.load(
            path
        )