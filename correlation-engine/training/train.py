from xgboost import XGBClassifier


class XGBoostTrainer:

    def __init__(
        self,
        random_state: int = 42,
    ):

        self.random_state = (
            random_state
        )

    def train(
        self,
        X_train,
        y_train,
    ):

        positive_count = int(
            y_train.sum()
        )

        negative_count = int(
            len(y_train)
            - positive_count
        )

        if positive_count == 0:

            raise ValueError(
                "O conjunto de treinamento não possui "
                "amostras positivas de causa raiz."
            )

        scale_pos_weight = (
            negative_count
            / positive_count
        )

        model = XGBClassifier(
            n_estimators=150,
            max_depth=5,
            learning_rate=0.08,

            objective=(
                "binary:logistic"
            ),

            eval_metric=(
                "logloss"
            ),

            scale_pos_weight=(
                scale_pos_weight
            ),

            random_state=(
                self.random_state
            ),

            n_jobs=-1,
        )

        model.fit(
            X_train,
            y_train,
        )

        return model