import logging
from collections import defaultdict

import pandas as pd
import numpy as np
from sklearn.metrics import f1_score, accuracy_score, roc_auc_score
from sklearn.multiclass import OneVsRestClassifier
from sklearn.model_selection import ShuffleSplit, StratifiedKFold, GridSearchCV
from sklearn import linear_model
from sklearn import preprocessing

from GAT2VEC import paths
from GAT2VEC import parsers

__all__ = ['Classification']

logger = logging.getLogger(__name__)


class Classification:
    """This class performs multi-class/multi-label classification tasks."""

    def __init__(self, dataset_dir, output_dir, tr, multilabel=False):
        self.dataset = paths.get_dataset_name(dataset_dir)
        self.output = {"TR": [], "accuracy": [], "f1micro": [], "f1macro": [], "auc": []}
        self.TR = tr  # the training ratio for classifier
        self.dataset_dir = dataset_dir
        self.output_dir = output_dir
        self.multi_label = multilabel
        if self.multi_label:
            self.labels, self.label_ind, self.label_count = parsers.get_multilabels(
                self.dataset_dir)
            self.labels = self.binarize_labels(self.labels)
        else:
            self.labels, self.label_ind, self.label_count = parsers.get_labels(self.dataset_dir)

    def binarize_labels(self, labels, nclasses=None):
        """ returns the multilabelbinarizer object"""
        if nclasses == None:
            mlb = preprocessing.MultiLabelBinarizer()
            return mlb.fit_transform(labels)
        # for fit_and_predict to return binarized object of predicted classes
        mlb = preprocessing.MultiLabelBinarizer(classes=range(nclasses))
        return mlb.fit_transform(labels)

    def evaluate(self, model, label=False, evaluation_scheme="tr"):
        embedding = 0
        clf = self.get_classifier()

        if not label:
            embedding = parsers.get_embeddingDF(model)

        if evaluation_scheme == "cv":
            results = self.evaluate_cv(clf, embedding, 5)
        elif evaluation_scheme == "tr" or label:
            results = defaultdict(list)
            for tr in self.TR:
                logger.debug("TR ... %s", tr)
                if label:
                    model = paths.get_embedding_path_wl(self.dataset_dir, self.output_dir, tr)
                    if isinstance(model, str):
                        embedding = parsers.get_embeddingDF(model)
                results.update(self.evaluate_tr(clf, embedding, tr))

        logger.debug("Training Finished")

        df = pd.DataFrame(results)
        return df.groupby(axis=0, by="TR").mean()

    def get_classifier(self):
        """ returns the classifier"""
        log_reg = linear_model.LogisticRegression(solver='lbfgs')
        ors = OneVsRestClassifier(log_reg)
        return ors

    def evaluate_tr(self, clf, embedding, tr):
        """ evaluates an embedding for classification on training ration of tr."""
        ss = ShuffleSplit(n_splits=10, train_size=tr, random_state=2)
        for train_idx, test_idx in ss.split(self.labels):
            X_train, X_test, Y_train, Y_test = self._get_split(embedding, test_idx, train_idx)
            pred, probs = self.get_predictions(clf, X_train, X_test, Y_train, Y_test)
            self.output["TR"].append(tr)
            self.output["accuracy"].append(accuracy_score(Y_test, pred))
            self.output["f1micro"].append(f1_score(Y_test, pred, average='micro'))
            self.output["f1macro"].append(f1_score(Y_test, pred, average='macro'))
            if self.label_count == 2:
                self.output["auc"].append(roc_auc_score(Y_test, probs[:, 1]))
            else:
                self.output["auc"].append(0)
        return self.output

    def evaluate_cv(self, clf, embedding, n_splits):
        """Do a repeated stratified cross validation.

        :param clf: Classifier object.
        :param embedding: The feature matrix.
        :param n_splits: Number of folds.
        :return: Dictionary containing numerical results of the classification.
        """
        embedding = embedding[self.label_ind, :]
        results = defaultdict(list)
        grid = {
            'C': np.logspace(-4, 4, 20),
            'tol': [0.0001, 0.001, 0.01]
        }
        log_reg = linear_model.LogisticRegression(solver='liblinear')

        # tol, C
        for i in range(10):
            inner_cv = StratifiedKFold(n_splits=n_splits, shuffle=True)
            outer_cv = StratifiedKFold(n_splits=n_splits, shuffle=True)

            for train_idx, test_idx in outer_cv.split(embedding, self.labels):
                clf = GridSearchCV(estimator=log_reg, param_grid=grid, cv=inner_cv, iid=False)
                clf.fit(embedding, self.labels)

                print('Parameter fitting done. clf: {}'.format(clf))

                X_train, X_test, Y_train, Y_test = self._get_split(embedding, test_idx, train_idx)
                pred, probs = self.get_predictions(clf, X_train, X_test, Y_train, Y_test)
                results["TR"].append(i)
                results["accuracy"].append(accuracy_score(Y_test, pred))
                results["f1micro"].append(f1_score(Y_test, pred, average='micro'))
                results["f1macro"].append(f1_score(Y_test, pred, average='macro'))
                if self.label_count == 2:
                    results["auc"].append(roc_auc_score(Y_test, probs[:, 1]))
                else:
                    results["auc"].append(0)
        return results

    def get_prediction_probs_for_entire_set(self, model):
        embedding = parsers.get_embeddingDF(model)
        embedding = embedding[self.label_ind, :]

        log_reg = linear_model.LogisticRegression(solver='lbfgs')
        clf = OneVsRestClassifier(log_reg)

        clf.fit(embedding, self.labels)  # for multi-class classification
        probs = clf.predict_proba(embedding)
        logger.debug('ROC: %.2f', roc_auc_score(self.labels, probs[:, 1]))

        return probs

    def _get_split(self, embedding, test_id, train_id):
        return embedding[train_id], embedding[test_id], self.labels[train_id], self.labels[test_id]

    def get_predictions(self, clf, X_train, X_test, Y_train, Y_test):
        if self.multi_label:
            return self.fit_and_predict_multilabel(clf, X_train, X_test, Y_train, Y_test)
        else:
            # clf.fit(X_train, Y_train)  # for multi-class classification
            print(clf)
            return clf.predict(X_test), clf.predict_proba(X_test)

    def fit_and_predict_multilabel(self, clf, X_train, X_test, y_train, y_test):
        """ predicts and returns the top k labels for multi-label classification
        k depends on the number of labels in y_test."""
        clf.fit(X_train, y_train)
        y_pred_probs = clf.predict_proba(X_test)

        pred_labels = []
        nclasses = y_test.shape[1]
        top_k_labels = [np.nonzero(label)[0].tolist() for label in y_test]
        for i in range(len(y_test)):
            k = len(top_k_labels[i])
            probs_ = y_pred_probs[i, :]
            labels_ = tuple(np.argsort(probs_).tolist()[-k:])
            pred_labels.append(labels_)
        return self.binarize_labels(pred_labels, nclasses), y_pred_probs
