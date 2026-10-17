
import sys

import matplotlib
import numpy as np
import openpyxl
import pandas as pd
import pulp
import pytest
import scipy
import sklearn
import statsmodels


def test_python_version():
    """Confirm the project runs on Python 3.13 or later."""
    assert sys.version_info >= (3, 13)


def test_core_libraries_import():
    """Confirm the required analytics libraries import successfully."""
    assert pd.__version__
    assert np.__version__
    assert matplotlib.__version__
    assert sklearn.__version__
    assert statsmodels.__version__
    assert scipy.__version__
    assert openpyxl.__version__
    assert pytest.__version__


def test_pulp_optimization_solver():
    """Solve a small optimization problem using PuLP and CBC."""
    problem = pulp.LpProblem("Project2_Environment_Check", pulp.LpMinimize)
    quantity = pulp.LpVariable("replenishment_quantity", lowBound=0)

    problem += quantity
    problem += quantity >= 5

    status = problem.solve(pulp.PULP_CBC_CMD(msg=False))

    assert pulp.LpStatus[status] == "Optimal"
    assert quantity.value() == pytest.approx(5.0)
