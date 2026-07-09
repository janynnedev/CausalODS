from xgboost import XGBClassifier



class XGBoostTrainer:



    def train(
        self,
        X_train,
        y_train
    ):


        model = XGBClassifier(

            n_estimators=100,

            max_depth=5,

            learning_rate=0.1,

            objective=
            "binary:logistic",

            eval_metric=
            "logloss"

        )


        model.fit(

            X_train,

            y_train

        )


        return model