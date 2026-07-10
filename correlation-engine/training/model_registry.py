import joblib

from config.settings import Settings



class ModelRegistry:



    def save(self, model, path=None):
        if path is None:
            path = Settings.MODEL_PATH

        joblib.dump(model, path)

    def load(self, path=None):
        if path is None:
            path = Settings.MODEL_PATH


        return joblib.load(path)