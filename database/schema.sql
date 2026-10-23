-- Project 02: AI Demand Forecasting and Inventory Optimization
-- Logical schema Version 1.3; SQLite physical schema baseline.
-- Currency: integer poisha (100 poisha = BDT 1).
-- All foreign keys use RESTRICT; no cascading actions.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS products (
    product_id INTEGER PRIMARY KEY,
    sku TEXT NOT NULL UNIQUE CHECK (length(trim(sku)) > 0),
    product_name TEXT NOT NULL CHECK (length(trim(product_name)) > 0),
    category TEXT NOT NULL CHECK (length(trim(category)) > 0),
    unit_cost INTEGER NOT NULL CHECK (unit_cost >= 0),
    selling_price INTEGER NOT NULL CHECK (selling_price >= 0),
    unit_of_measure TEXT NOT NULL CHECK (length(trim(unit_of_measure)) > 0),
    is_active INTEGER NOT NULL CHECK (is_active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS locations (
    location_id INTEGER PRIMARY KEY,
    location_code TEXT NOT NULL UNIQUE CHECK (length(trim(location_code)) > 0),
    location_name TEXT NOT NULL CHECK (length(trim(location_name)) > 0),
    location_type TEXT,
    region TEXT NOT NULL CHECK (length(trim(region)) > 0),
    is_active INTEGER NOT NULL CHECK (is_active IN (0, 1)),
    CHECK (location_type IS NULL OR length(trim(location_type)) > 0)
);

CREATE TABLE IF NOT EXISTS suppliers (
    supplier_id INTEGER PRIMARY KEY,
    supplier_code TEXT NOT NULL UNIQUE CHECK (length(trim(supplier_code)) > 0),
    supplier_name TEXT NOT NULL CHECK (length(trim(supplier_name)) > 0),
    contact_reference TEXT,
    default_lead_time_days INTEGER CHECK (default_lead_time_days IS NULL OR default_lead_time_days >= 0),
    is_active INTEGER NOT NULL CHECK (is_active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS supplier_products (
    supplier_product_id INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    unit_purchase_cost INTEGER NOT NULL CHECK (unit_purchase_cost >= 0),
    lead_time_days INTEGER NOT NULL CHECK (lead_time_days >= 0),
    min_order_qty INTEGER NOT NULL CHECK (min_order_qty > 0),
    order_multiple INTEGER NOT NULL CHECK (order_multiple > 0),
    is_preferred INTEGER NOT NULL CHECK (is_preferred IN (0, 1)),
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    UNIQUE (supplier_id, product_id, effective_from),
    CHECK (effective_to IS NULL OR effective_to >= effective_from),
    FOREIGN KEY (supplier_id) REFERENCES suppliers(supplier_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE RESTRICT ON UPDATE RESTRICT
);

CREATE TABLE IF NOT EXISTS demand_history (
    demand_id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL,
    location_id INTEGER NOT NULL,
    demand_date TEXT NOT NULL,
    units_demanded INTEGER NOT NULL CHECK (units_demanded >= 0),
    units_sold INTEGER NOT NULL CHECK (units_sold >= 0 AND units_sold <= units_demanded),
    stockout_flag INTEGER NOT NULL CHECK (stockout_flag IN (0, 1)),
    estimated_lost_demand INTEGER NOT NULL CHECK (estimated_lost_demand >= 0),
    sales_revenue INTEGER NOT NULL CHECK (sales_revenue >= 0),
    UNIQUE (product_id, location_id, demand_date),
    FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    FOREIGN KEY (location_id) REFERENCES locations(location_id) ON DELETE RESTRICT ON UPDATE RESTRICT
);

CREATE TABLE IF NOT EXISTS inventory_snapshots (
    snapshot_id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL,
    location_id INTEGER NOT NULL,
    snapshot_time TEXT NOT NULL,
    on_hand_qty INTEGER NOT NULL CHECK (on_hand_qty >= 0),
    reserved_qty INTEGER NOT NULL CHECK (reserved_qty >= 0 AND reserved_qty <= on_hand_qty),
    on_order_qty INTEGER NOT NULL CHECK (on_order_qty >= 0),
    backorder_qty INTEGER NOT NULL CHECK (backorder_qty >= 0),
    UNIQUE (product_id, location_id, snapshot_time),
    FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    FOREIGN KEY (location_id) REFERENCES locations(location_id) ON DELETE RESTRICT ON UPDATE RESTRICT
);

CREATE TABLE IF NOT EXISTS inventory_movements (
    movement_id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL,
    location_id INTEGER NOT NULL,
    movement_time TEXT NOT NULL,
    movement_type TEXT NOT NULL CHECK (movement_type IN (
        'receipt', 'sale', 'customer_return', 'supplier_return',
        'adjustment_increase', 'adjustment_decrease', 'transfer_in',
        'transfer_out', 'damage_or_writeoff'
    )),
    quantity_delta INTEGER NOT NULL CHECK (quantity_delta <> 0),
    reference_type TEXT,
    reference_id TEXT,
    notes TEXT,
    CHECK (
        (movement_type IN ('receipt', 'customer_return', 'adjustment_increase', 'transfer_in') AND quantity_delta > 0)
        OR
        (movement_type IN ('sale', 'supplier_return', 'adjustment_decrease', 'transfer_out', 'damage_or_writeoff') AND quantity_delta < 0)
    ),
    FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    FOREIGN KEY (location_id) REFERENCES locations(location_id) ON DELETE RESTRICT ON UPDATE RESTRICT
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    purchase_order_id INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL,
    order_date TEXT NOT NULL,
    expected_delivery_date TEXT,
    actual_delivery_date TEXT,
    status TEXT NOT NULL CHECK (status IN ('draft', 'approved', 'ordered', 'partially_received', 'received', 'cancelled')),
    external_reference TEXT UNIQUE,
    CHECK (expected_delivery_date IS NULL OR expected_delivery_date >= order_date),
    FOREIGN KEY (supplier_id) REFERENCES suppliers(supplier_id) ON DELETE RESTRICT ON UPDATE RESTRICT
);

CREATE TABLE IF NOT EXISTS purchase_order_lines (
    purchase_order_line_id INTEGER PRIMARY KEY,
    purchase_order_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    ordered_qty INTEGER NOT NULL CHECK (ordered_qty > 0),
    received_qty INTEGER NOT NULL DEFAULT 0 CHECK (received_qty >= 0 AND received_qty <= ordered_qty),
    cancelled_qty INTEGER NOT NULL DEFAULT 0 CHECK (cancelled_qty >= 0 AND received_qty + cancelled_qty <= ordered_qty),
    unit_cost INTEGER NOT NULL CHECK (unit_cost >= 0),
    UNIQUE (purchase_order_id, product_id),
    FOREIGN KEY (purchase_order_id) REFERENCES purchase_orders(purchase_order_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE RESTRICT ON UPDATE RESTRICT
);

CREATE TABLE IF NOT EXISTS forecast_runs (
    forecast_run_id INTEGER PRIMARY KEY,
    run_time TEXT NOT NULL,
    model_name TEXT NOT NULL CHECK (length(trim(model_name)) > 0),
    model_version TEXT NOT NULL CHECK (length(trim(model_version)) > 0),
    training_start_date TEXT,
    training_end_date TEXT,
    forecast_start_date TEXT NOT NULL,
    forecast_end_date TEXT NOT NULL,
    evaluation_config TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    CHECK (training_start_date IS NULL OR training_end_date IS NULL OR training_end_date >= training_start_date),
    CHECK (forecast_end_date >= forecast_start_date)
);

CREATE TABLE IF NOT EXISTS forecast_results (
    forecast_result_id INTEGER PRIMARY KEY,
    forecast_run_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    location_id INTEGER NOT NULL,
    target_date TEXT NOT NULL,
    forecast_qty REAL NOT NULL CHECK (forecast_qty >= 0),
    UNIQUE (forecast_run_id, product_id, location_id, target_date),
    FOREIGN KEY (forecast_run_id) REFERENCES forecast_runs(forecast_run_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    FOREIGN KEY (location_id) REFERENCES locations(location_id) ON DELETE RESTRICT ON UPDATE RESTRICT
);

CREATE TABLE IF NOT EXISTS forecast_evaluations (
    forecast_evaluation_id INTEGER PRIMARY KEY,
    forecast_run_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    location_id INTEGER NOT NULL,
    evaluation_start_date TEXT NOT NULL,
    evaluation_end_date TEXT NOT NULL,
    horizon_days INTEGER NOT NULL CHECK (horizon_days > 0),
    mae REAL NOT NULL CHECK (mae >= 0),
    rmse REAL NOT NULL CHECK (rmse >= 0),
    wape REAL CHECK (wape IS NULL OR wape >= 0),
    forecast_bias REAL NOT NULL,
    UNIQUE (forecast_run_id, product_id, location_id, evaluation_start_date, evaluation_end_date, horizon_days),
    CHECK (evaluation_end_date >= evaluation_start_date),
    FOREIGN KEY (forecast_run_id) REFERENCES forecast_runs(forecast_run_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    FOREIGN KEY (location_id) REFERENCES locations(location_id) ON DELETE RESTRICT ON UPDATE RESTRICT
);

CREATE TABLE IF NOT EXISTS planning_runs (
    planning_run_id INTEGER PRIMARY KEY,
    planning_date TEXT NOT NULL,
    scenario_name TEXT NOT NULL CHECK (length(trim(scenario_name)) > 0),
    forecast_run_id INTEGER,
    scenario_config TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    FOREIGN KEY (forecast_run_id) REFERENCES forecast_runs(forecast_run_id) ON DELETE RESTRICT ON UPDATE RESTRICT
);

CREATE TABLE IF NOT EXISTS replenishment_recommendations (
    recommendation_id INTEGER PRIMARY KEY,
    planning_run_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    location_id INTEGER NOT NULL,
    supplier_product_id INTEGER NOT NULL,
    recommendation_date TEXT NOT NULL,
    reorder_point INTEGER NOT NULL CHECK (reorder_point >= 0),
    recommended_qty INTEGER NOT NULL CHECK (recommended_qty >= 0),
    estimated_cost INTEGER NOT NULL CHECK (estimated_cost >= 0),
    status TEXT NOT NULL CHECK (status IN ('proposed', 'approved', 'rejected', 'ordered', 'cancelled')),
    UNIQUE (planning_run_id, product_id, location_id, supplier_product_id),
    FOREIGN KEY (planning_run_id) REFERENCES planning_runs(planning_run_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    FOREIGN KEY (location_id) REFERENCES locations(location_id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    FOREIGN KEY (supplier_product_id) REFERENCES supplier_products(supplier_product_id) ON DELETE RESTRICT ON UPDATE RESTRICT
);

-- Initial indexes for common joins and filters. UNIQUE constraints already index the composite keys.
CREATE INDEX IF NOT EXISTS idx_supplier_products_product_effective ON supplier_products(product_id, effective_from, effective_to);
CREATE INDEX IF NOT EXISTS idx_demand_history_date ON demand_history(demand_date);
CREATE INDEX IF NOT EXISTS idx_inventory_snapshots_time ON inventory_snapshots(snapshot_time);
CREATE INDEX IF NOT EXISTS idx_inventory_movements_time ON inventory_movements(movement_time);
CREATE INDEX IF NOT EXISTS idx_purchase_orders_supplier_date ON purchase_orders(supplier_id, order_date);
CREATE INDEX IF NOT EXISTS idx_purchase_orders_status_expected ON purchase_orders(status, expected_delivery_date);
CREATE INDEX IF NOT EXISTS idx_forecast_runs_status_time ON forecast_runs(status, run_time);
CREATE INDEX IF NOT EXISTS idx_planning_runs_status_date ON planning_runs(status, planning_date);
CREATE INDEX IF NOT EXISTS idx_replenishment_recommendations_status_date ON replenishment_recommendations(status, recommendation_date);
