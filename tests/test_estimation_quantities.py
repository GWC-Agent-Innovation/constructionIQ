import unittest

from agent.layout_solver import solve_layout
from agent.schemas import HouseSpec
from estimation.quantity_calculator import calculate_quantities


class EstimationQuantityTests(unittest.TestCase):
    def setUp(self):
        spec = HouseSpec(plot_width_ft=40, plot_length_ft=60, num_bedrooms=2, num_bathrooms=1, entrance_facing="south")
        self.layout = solve_layout(spec)
        self.assertIsNotNone(self.layout)

    def test_returns_positive_quantities(self):
        result = calculate_quantities(self.layout)
        self.assertEqual([item["material"] for item in result], ["Bricks / blocks", "Cement for masonry", "Steel reinforcement bars"])
        self.assertTrue(all(item["quantity"] > 0 and item["basis"] for item in result))

    def test_wastage_is_customizable(self):
        normal = calculate_quantities(self.layout)
        no_waste = calculate_quantities(self.layout, {"brick_wastage": 0, "steel_wastage": 0})
        self.assertGreater(normal[0]["quantity"], no_waste[0]["quantity"])
        self.assertGreater(normal[2]["quantity"], no_waste[2]["quantity"])

    def test_invalid_inputs_rejected(self):
        with self.assertRaises(ValueError):
            calculate_quantities(self.layout, {"wall_height_ft": -1})
        with self.assertRaises(ValueError):
            calculate_quantities(None)


if __name__ == "__main__":
    unittest.main()
