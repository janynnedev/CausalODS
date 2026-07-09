from sklearn.metrics import (

    accuracy_score,

    precision_score,

    recall_score,

    f1_score,

    confusion_matrix

)



class Evaluator:



    def evaluate(

        self,

        model,

        X_test,

        y_test

    ):


        prediction = model.predict(
            X_test
        )


        return {


            "accuracy":

                accuracy_score(
                    y_test,
                    prediction
                ),



            "precision":

                precision_score(
                    y_test,
                    prediction,
                    zero_division=0
                ),



            "recall":

                recall_score(
                    y_test,
                    prediction,
                    zero_division=0
                ),



            "f1":

                f1_score(
                    y_test,
                    prediction,
                    zero_division=0
                ),



            "confusion_matrix":

                confusion_matrix(
                    y_test,
                    prediction
                ).tolist()

        }