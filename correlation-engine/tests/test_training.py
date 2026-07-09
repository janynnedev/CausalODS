from sklearn.model_selection import train_test_split


from training.data_loader import DataLoader

from training.preprocessing import Preprocessor

from training.train import XGBoostTrainer

from training.evaluator import Evaluator

from training.model_registry import ModelRegistry



loader = DataLoader()


df = loader.load(
    "results_rca_dataset.csv"
)



preprocessor = Preprocessor()


X,y = preprocessor.prepare(
    df
)



X_train,X_test,y_train,y_test = train_test_split(

    X,

    y,

    test_size=0.2,

    random_state=42

)



trainer = XGBoostTrainer()


model = trainer.train(

    X_train,

    y_train

)



evaluator = Evaluator()


metrics = evaluator.evaluate(

    model,

    X_test,

    y_test

)



print(metrics)



registry = ModelRegistry()


registry.save(
    model
)