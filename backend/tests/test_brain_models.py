"""Model tests: both candidates learn a separable signal and round-trip."""
import unittest

import numpy as np

from backend.brain import models, validation
from backend.brain.scaler import StandardScaler


def _separable(n=600, d=6, seed=0):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, d))
    w = np.array([2.0, -1.5, 1.0, 0.0, 0.0, 0.5])[:d]
    logit = X @ w
    y = (rng.uniform(size=n) < 1 / (1 + np.exp(-logit))).astype(int)
    return X, y


class TestModels(unittest.TestCase):
    def setUp(self):
        X, y = _separable()
        n_tr = 450
        scaler = StandardScaler().fit(X[:n_tr], [f"f{i}" for i in range(X.shape[1])])
        self.Xtr, self.ytr = scaler.transform(X[:n_tr]), y[:n_tr]
        self.Xte, self.yte = scaler.transform(X[n_tr:]), y[n_tr:]

    def test_logreg_learns(self):
        m = models.LogisticRegressionNP().fit(self.Xtr, self.ytr)
        p = m.predict_proba(self.Xte)
        self.assertEqual(p.shape, (self.Xte.shape[0],))
        self.assertTrue(np.all((p >= 0) & (p <= 1)))
        self.assertGreater(validation.roc_auc(self.yte, p), 0.70)

    def test_mlp_learns(self):
        m = models.FinancialRankingNet(hidden=16, epochs=400).fit(self.Xtr, self.ytr)
        p = m.predict_proba(self.Xte)
        self.assertGreater(validation.roc_auc(self.yte, p), 0.70)

    def test_determinism(self):
        m1 = models.FinancialRankingNet(epochs=50, seed=42).fit(self.Xtr, self.ytr)
        m2 = models.FinancialRankingNet(epochs=50, seed=42).fit(self.Xtr, self.ytr)
        np.testing.assert_allclose(m1.predict_proba(self.Xte), m2.predict_proba(self.Xte))

    def test_serialization_roundtrip(self):
        for ctor in (models.LogisticRegressionNP, models.FinancialRankingNet):
            m = ctor().fit(self.Xtr, self.ytr)
            restored = models.model_from_dict(m.to_dict())
            np.testing.assert_allclose(
                m.predict_proba(self.Xte), restored.predict_proba(self.Xte), rtol=1e-10
            )

    def test_build_model_unknown(self):
        with self.assertRaises(ValueError):
            models.build_model("does-not-exist")


if __name__ == "__main__":
    unittest.main()
