# Data Dictionary — AI Demand Forecasting and Inventory Optimization

**Document version:** 1.3 — Cancellation and integrity corrections  
**Database target:** SQLite  
**Implementation language:** Python  
**Data strategy:** Reproducible synthetic data, with optional public-data comparison  
**Currency:** BDT, stored as integer poisha (100 poisha = ৳1)  
**Status:** Logical schema for final cross-document review; do not implement SQLite until the ERD and data dictionary pass the acceptance checklist  
**Related document:** `docs/database_erd.md`

---

## 1. Purpose and conventions

This data dictionary defines the exact columns, SQLite types, requiredness, keys, constraints, units, controlled values, and business meanings for the 14 tables in `docs/database_erd.md`. Both documents must remain synchronized. Any schema change must be made in both documents and rechecked before implementation.

### 1.1 General conventions

- `INTEGER PRIMARY KEY` is the surrogate identifier for each table.
- `TEXT` dates use ISO `YYYY-MM-DD`.
- `TEXT` timestamps use ISO 8601 UTC.
- `INTEGER` quantities are in the product's base unit unless otherwise specified.
- `REAL` is used for fractional forecast quantities and error metrics, not money.
- SQLite `CHECK` constraints should be paired with Python validation for rules SQLite cannot conveniently enforce, including date parsing, status transitions, JSON validity, overlapping effective-date ranges, and finite-number validation for REAL values.
- Boolean values are stored as integer `0` or `1`.
- “Required: Yes” means `NOT NULL`; “Required: No” means nullable.
- Every foreign key uses `ON DELETE RESTRICT ON UPDATE RESTRICT`.
- No cascading actions are permitted, and routine physical deletion of business records is prohibited.

### 1.2 Monetary values

All monetary values use integer poisha. One Bangladeshi taka equals 100 poisha. Do not store money in `REAL`. Divide by 100 only when presenting values in taka. Fields storing costs or revenue must be non-negative unless a later, documented business rule explicitly supports a reversal/credit record.

### 1.3 Demand and inventory terminology

- **Underlying demand:** customer demand before availability constraints.
- **Units sold:** demand fulfilled by sales.
- **Estimated lost demand:** estimated demand that could not be fulfilled due to stock availability.
- **Confirmed zero demand:** an observed date with `units_demanded = 0`.
- **Missing/unknown demand date:** no trustworthy observation exists; it must not be silently interpreted as zero.
- **On-hand quantity:** physical inventory currently present.
- **Reserved quantity:** quantity allocated within on-hand stock; do not add it to on-hand.
- **On-order quantity:** eligible outstanding purchase-order quantity.
- **Backorder quantity:** unfulfilled demand carried forward.
- **Inventory position:** `on_hand_qty - reserved_qty + on_order_qty - backorder_qty`. It may be negative.

### 1.4 Historical preservation policy

Routine pipeline runs must not physically delete historical business records. Primary keys are immutable. Product, location, and supplier deactivation uses `is_active`; purchase-order and recommendation cancellation uses status values. Corrections must preserve the original record and be represented through documented correction/audit mechanisms or new records. Supplier terms are effective-dated; a new term version is inserted rather than overwriting historical terms. Failed processing must not destroy historical records or replace previously published outputs.

### 1.5 Foreign-key and referential-action policy

Enable `PRAGMA foreign_keys = ON` for every SQLite connection and test that it is active. Every foreign key uses `ON DELETE RESTRICT ON UPDATE RESTRICT`. Do not use `CASCADE`, `SET NULL`, or `SET DEFAULT` actions. Application workflows must not physically delete business records even where no foreign key currently references them.

## 2. Table: `products`

**Purpose:** Product master data.  
**Grain:** One row per product.

| Column | SQLite type | Required? | Key / constraint | Definition |
|---|---|---:|---|---|
| `product_id` | INTEGER | Yes | PK | Internal product identifier |
| `sku` | TEXT | Yes | UNIQUE, non-empty | Stable stock-keeping unit |
| `product_name` | TEXT | Yes | Non-empty | Human-readable product name |
| `category` | TEXT | Yes | Non-empty | Product category for analysis |
| `unit_cost` | INTEGER | Yes | CHECK >= 0 | Reference cost per base unit, in poisha |
| `selling_price` | INTEGER | Yes | CHECK >= 0 | Reference selling price per base unit, in poisha |
| `unit_of_measure` | TEXT | Yes | Non-empty | Base unit, e.g. piece, pack, kilogram |
| `is_active` | INTEGER | Yes | CHECK IN (0, 1) | Whether the product is currently offered |

Rules:
- `sku` must be unique and non-empty.
- `unit_cost` and `selling_price` are integer poisha.
- Deactivation must not remove historical demand, inventory, forecast, or transaction records.

## 3. Table: `locations`

**Purpose:** Warehouses, stores, distribution centers, or other inventory locations.  
**Grain:** One row per location.

| Column | SQLite type | Required? | Key / constraint | Definition |
|---|---|---:|---|---|
| `location_id` | INTEGER | Yes | PK | Internal location identifier |
| `location_code` | TEXT | Yes | UNIQUE, non-empty | Stable location code |
| `location_name` | TEXT | Yes | Non-empty | Human-readable location name |
| `location_type` | TEXT | No | Controlled vocabulary in application validation | Type such as warehouse, store, distribution center |
| `region` | TEXT | Yes | Non-empty | Geographic or operational region |
| `is_active` | INTEGER | Yes | CHECK IN (0, 1) | Whether the location is currently active |

Rules:
- `location_code` must be unique and non-empty.
- `region` is required for consistent geographic/operational reporting.
- Deactivation must not remove historical records.

## 4. Table: `suppliers`

**Purpose:** Supplier master data.  
**Grain:** One row per supplier.

| Column | SQLite type | Required? | Key / constraint | Definition |
|---|---|---:|---|---|
| `supplier_id` | INTEGER | Yes | PK | Internal supplier identifier |
| `supplier_code` | TEXT | Yes | UNIQUE, non-empty | Stable supplier code |
| `supplier_name` | TEXT | Yes | Non-empty | Human-readable supplier name |
| `contact_reference` | TEXT | No | — | Optional non-sensitive contact reference |
| `default_lead_time_days` | INTEGER | No | CHECK >= 0 | Default lead time in calendar days |
| `is_active` | INTEGER | Yes | CHECK IN (0, 1) | Whether the supplier is currently active |

Rules:
- `supplier_code` must be unique and non-empty.
- Supplier-specific terms belong in `supplier_products`; `default_lead_time_days` is a fallback only.
- Deactivation must not remove historical purchase orders or supplier terms.

## 5. Table: `supplier_products`

**Purpose:** Effective-dated supplier-specific purchasing terms.  
**Grain:** One row per supplier, product, and effective period.

| Column | SQLite type | Required? | Key / constraint | Definition |
|---|---|---:|---|---|
| `supplier_product_id` | INTEGER | Yes | PK | Unique supplier-product terms-version identifier |
| `supplier_id` | INTEGER | Yes | FK → `suppliers.supplier_id` | Supplier offering the product |
| `product_id` | INTEGER | Yes | FK → `products.product_id` | Product supplied |
| `unit_purchase_cost` | INTEGER | Yes | CHECK >= 0 | Supplier-specific unit purchase cost in poisha |
| `lead_time_days` | INTEGER | Yes | CHECK >= 0 | Expected replenishment lead time in calendar days |
| `min_order_qty` | INTEGER | Yes | CHECK > 0 | Minimum permitted order quantity in base units |
| `order_multiple` | INTEGER | Yes | CHECK > 0 | Permitted order quantity increment in base units |
| `is_preferred` | INTEGER | Yes | CHECK IN (0, 1) | Whether this supplier-product option is preferred |
| `effective_from` | TEXT | Yes | ISO date | Inclusive start date of these terms |
| `effective_to` | TEXT | No | ISO date; >= `effective_from` when present | Inclusive end date; NULL means open-ended |

Rules:
- Unique key: (`supplier_id`, `product_id`, `effective_from`).
- It is valid to have multiple records for the same supplier-product pair across different effective periods.
- Effective periods for the same supplier-product pair must not overlap. Enforce this in application validation/tests.
- `effective_to` is inclusive and must be on or after `effective_from`.
- Do not overwrite historical supplier terms when prices, lead times, minimum quantities, or order multiples change; insert a new effective-dated row.
- Planning selects the row valid on the planning run's `planning_date`.
- A row with `effective_to = NULL` is open-ended; no later period for the same supplier-product pair can overlap it unless it is first closed through an approved historical correction process.

## 6. Table: `demand_history`

**Purpose:** Underlying demand, fulfilled sales, stockout flag, estimated lost demand, and sales revenue.  
**Grain:** One row per product, location, and demand date.

| Column | SQLite type | Required? | Key / constraint | Definition |
|---|---|---:|---|---|
| `demand_id` | INTEGER | Yes | PK | Internal demand-record identifier |
| `product_id` | INTEGER | Yes | FK → `products.product_id` | Product demanded |
| `location_id` | INTEGER | Yes | FK → `locations.location_id` | Location where demand occurred |
| `demand_date` | TEXT | Yes | ISO date | Demand date in `YYYY-MM-DD` format |
| `units_demanded` | INTEGER | Yes | CHECK >= 0 | Underlying customer demand in base units |
| `units_sold` | INTEGER | Yes | CHECK >= 0 | Fulfilled sales in base units |
| `stockout_flag` | INTEGER | Yes | CHECK IN (0, 1) | Whether a stockout affected the observation |
| `estimated_lost_demand` | INTEGER | Yes | CHECK >= 0 | Estimated unfulfilled demand in base units |
| `sales_revenue` | INTEGER | Yes | CHECK >= 0 | Revenue from fulfilled sales, in poisha |

Rules:
- Unique key: (`product_id`, `location_id`, `demand_date`).
- `units_demanded`, `units_sold`, and `estimated_lost_demand` are non-negative integers.
- `units_sold` must not exceed `units_demanded` unless an explicit, documented rule for substitutions/adjustments permits it.
- `stockout_flag` must be `0` or `1`.
- `sales_revenue` is integer poisha and represents fulfilled-sales revenue, not hypothetical lost-sales revenue.
- Confirmed zero-demand dates require a row with `units_demanded = 0`.
- Missing/unknown dates must not silently become zero-demand rows.
- Forecasting must reject or flag unresolved gaps. The synthetic generator creates a complete daily series per product-location combination.

## 7. Table: `inventory_snapshots`

**Purpose:** Inventory balances at specific points in time.  
**Grain:** One row per product, location, and snapshot timestamp.

| Column | SQLite type | Required? | Key / constraint | Definition |
|---|---|---:|---|---|
| `snapshot_id` | INTEGER | Yes | PK | Internal snapshot identifier |
| `product_id` | INTEGER | Yes | FK → `products.product_id` | Product being counted |
| `location_id` | INTEGER | Yes | FK → `locations.location_id` | Location being counted |
| `snapshot_time` | TEXT | Yes | ISO 8601 UTC timestamp | Time of inventory snapshot |
| `on_hand_qty` | INTEGER | Yes | CHECK >= 0 | Physical inventory quantity |
| `reserved_qty` | INTEGER | Yes | CHECK >= 0 and <= `on_hand_qty` | Quantity reserved within on-hand stock |
| `on_order_qty` | INTEGER | Yes | CHECK >= 0 | Eligible outstanding purchase-order quantity |
| `backorder_qty` | INTEGER | Yes | CHECK >= 0 | Unfulfilled demand quantity carried forward |

Rules:
- Unique key: (`product_id`, `location_id`, `snapshot_time`).
- `reserved_qty` must not exceed `on_hand_qty`.
- Inventory position is `on_hand_qty - reserved_qty + on_order_qty - backorder_qty`.
- Reserved stock is already included in on-hand stock and is deducted once.
- On-order quantity includes eligible outstanding quantities only; cancelled and fully received quantities are excluded.
- Inventory position may be negative.

## 8. Table: `inventory_movements`

**Purpose:** Inventory-affecting events.  
**Grain:** One row per inventory movement event.

| Column | SQLite type | Required? | Key / constraint | Definition |
|---|---|---:|---|---|
| `movement_id` | INTEGER | Yes | PK | Internal movement identifier |
| `product_id` | INTEGER | Yes | FK → `products.product_id` | Product affected |
| `location_id` | INTEGER | Yes | FK → `locations.location_id` | Location affected |
| `movement_time` | TEXT | Yes | ISO 8601 UTC timestamp | Time of movement |
| `movement_type` | TEXT | Yes | Controlled vocabulary | Type of inventory event |
| `quantity_delta` | INTEGER | Yes | CHECK <> 0 | Signed change in physical stock |
| `reference_type` | TEXT | No | — | Source transaction type |
| `reference_id` | TEXT | No | — | Source transaction identifier |
| `notes` | TEXT | No | — | Optional explanatory note |

Initial `movement_type` vocabulary:
- `receipt`
- `sale`
- `customer_return`
- `supplier_return`
- `adjustment_increase`
- `adjustment_decrease`
- `transfer_in`
- `transfer_out`
- `damage_or_writeoff`

Rules:
- Positive `quantity_delta` adds physical stock; negative values reduce it.
- `quantity_delta` cannot be zero in the baseline.
- Positive `quantity_delta` is required for `receipt`, `customer_return`, `adjustment_increase`, and `transfer_in`.
- Negative `quantity_delta` is required for `sale`, `supplier_return`, `adjustment_decrease`, `transfer_out`, and `damage_or_writeoff`.
- Validate the movement-type/sign pairing in Python and tests. These mappings are the baseline synthetic-model assumption; exceptional business cases require an explicit schema/rule revision.
- `reference_type` and `reference_id` form a polymorphic reference, not a conventional foreign key. Validate the reference in application logic or redesign it if database-enforced referential integrity is required.

## 9. Table: `purchase_orders`

**Purpose:** Purchase-order headers.  
**Grain:** One row per purchase order.

| Column | SQLite type | Required? | Key / constraint | Definition |
|---|---|---:|---|---|
| `purchase_order_id` | INTEGER | Yes | PK | Internal purchase-order identifier |
| `supplier_id` | INTEGER | Yes | FK → `suppliers.supplier_id` | Supplier receiving the order |
| `order_date` | TEXT | Yes | ISO date | Date the order was placed/recorded |
| `expected_delivery_date` | TEXT | No | ISO date | Expected delivery date, if known |
| `actual_delivery_date` | TEXT | No | ISO date | Actual completion/delivery date, if known |
| `status` | TEXT | Yes | Controlled vocabulary | Current order lifecycle status |
| `external_reference` | TEXT | No | UNIQUE when non-NULL | External purchase-order reference |

Status values and transitions:
- `draft` → `approved` or `cancelled`
- `approved` → `ordered` or `cancelled`
- `ordered` → `partially_received`, `received`, or `cancelled`
- `partially_received` → `received` or `cancelled`
- `received` and `cancelled` are terminal

Rules:
- Supplier must exist.
- Expected delivery date must not precede order date unless a documented exception exists.
- Receipt quantities are tracked on `purchase_order_lines`.
- `received` means every ordered unit has been received and `cancelled_qty` is zero.
- On cancellation, set each line’s `cancelled_qty` to the quantity no longer expected so `received_qty + cancelled_qty = ordered_qty`; preserve receipt history. A partially received order may be cancelled only after this reconciliation.
- Draft and cancelled orders are retained for auditability.
- Only outstanding quantities on eligible `ordered` or `partially_received` lines contribute to `on_order_qty`; cancelled, draft, approved-but-not-ordered, and fully received quantities are excluded.

## 10. Table: `purchase_order_lines`

**Purpose:** Products, quantities, and costs belonging to purchase orders.  
**Grain:** One row per product line within a purchase order.

| Column | SQLite type | Required? | Key / constraint | Definition |
|---|---|---:|---|---|
| `purchase_order_line_id` | INTEGER | Yes | PK | Internal purchase-order-line identifier |
| `purchase_order_id` | INTEGER | Yes | FK → `purchase_orders.purchase_order_id` | Parent purchase order |
| `product_id` | INTEGER | Yes | FK → `products.product_id` | Product ordered |
| `ordered_qty` | INTEGER | Yes | CHECK > 0 | Quantity ordered in base units |
| `received_qty` | INTEGER | Yes | DEFAULT 0; CHECK >= 0 and <= `ordered_qty` | Cumulative quantity received |
| `cancelled_qty` | INTEGER | Yes | DEFAULT 0; CHECK >= 0 and `received_qty` + `cancelled_qty` <= `ordered_qty` | Cumulative quantity explicitly cancelled and no longer expected |
| `unit_cost` | INTEGER | Yes | CHECK >= 0 | Agreed purchase cost per unit, in poisha |

Rules:
- Unique key: (`purchase_order_id`, `product_id`); duplicate product lines are not allowed in the baseline.
- `ordered_qty` must be positive.
- `received_qty` and `cancelled_qty` start at zero and must each be non-negative. Their sum must not exceed `ordered_qty`.
- `cancelled_qty` explicitly records quantities no longer expected; it is required to reconcile cancellation of an order that was partially received.
- For a `cancelled` purchase order, every line must satisfy `received_qty + cancelled_qty = ordered_qty`. For a `received` purchase order, every line must satisfy `received_qty = ordered_qty` and `cancelled_qty = 0`.
- For `draft`, `approved`, `ordered`, and `partially_received` orders, quantities must remain within the general sum constraint; `cancelled_qty` must be zero until cancellation is recorded.
- Eligible outstanding quantity for `ordered` or `partially_received` orders is `ordered_qty - received_qty - cancelled_qty`; only this quantity contributes to `on_order_qty`.
- Partial deliveries update cumulative `received_qty`; detailed receipt-event history is deferred scope.
- `unit_cost` is historical transaction cost in integer poisha.
- Line-level expected delivery dates are deferred; use the header's expected delivery date.
- No `line_status` column is included. Receipt state is derived from quantities and parent order status.

## 11. Table: `forecast_runs`

**Purpose:** Metadata and configuration for each demand-forecasting execution.  
**Grain:** One row per forecast run.

| Column | SQLite type | Required? | Key / constraint | Definition |
|---|---|---:|---|---|
| `forecast_run_id` | INTEGER | Yes | PK | Internal forecast-run identifier |
| `run_time` | TEXT | Yes | ISO 8601 UTC timestamp | Time execution was recorded |
| `model_name` | TEXT | Yes | Non-empty | Model identifier, e.g. `seasonal_naive` |
| `model_version` | TEXT | Yes | Non-empty | Model or code version |
| `training_start_date` | TEXT | No | ISO date | Start of training window |
| `training_end_date` | TEXT | No | ISO date | End of training window |
| `forecast_start_date` | TEXT | Yes | ISO date | First target date in forecast horizon |
| `forecast_end_date` | TEXT | Yes | ISO date; >= start | Last target date in forecast horizon |
| `evaluation_config` | TEXT | Yes | Valid JSON | Evaluation settings and reproducibility configuration |
| `status` | TEXT | Yes | Controlled vocabulary | Forecast-run lifecycle status |

Status values: `pending`, `running`, `completed`, `failed`.

Rules:
- Allowed transitions: `pending` → `running` → `completed` or `failed`.
- A failed run cannot be changed to completed; a retry creates a new run.
- If both training dates are present, `training_end_date` must be on or after `training_start_date`.
- `forecast_end_date` must be on or after `forecast_start_date`.
- `evaluation_config` must parse as valid JSON and include relevant horizon/training/evaluation settings.
- Record random seeds and input/code version information when relevant.
- A completed run must have validated forecast results.

## 12. Table: `forecast_results`

**Purpose:** Forecast values produced by a forecast run.  
**Grain:** One row per forecast run, product, location, and target date.

| Column | SQLite type | Required? | Key / constraint | Definition |
|---|---|---:|---|---|
| `forecast_result_id` | INTEGER | Yes | PK | Internal forecast-result identifier |
| `forecast_run_id` | INTEGER | Yes | FK → `forecast_runs.forecast_run_id` | Run that produced the forecast |
| `product_id` | INTEGER | Yes | FK → `products.product_id` | Forecast product |
| `location_id` | INTEGER | Yes | FK → `locations.location_id` | Forecast location |
| `target_date` | TEXT | Yes | ISO date | Date to which the forecast applies |
| `forecast_qty` | REAL | Yes | CHECK >= 0 and finite | Predicted demand in base units |

Unique key: (`forecast_run_id`, `product_id`, `location_id`, `target_date`).

Rules:
- Forecast quantities must be non-negative and finite.
- A forecast result target date must fall within the run's configured forecast window.
- Fractional model output is permitted; replenishment recommendations use integer quantities after documented rounding and supplier constraints.

## 13. Table: `forecast_evaluations`

**Purpose:** Forecast accuracy metrics for a defined scope.  
**Grain:** One row per forecast run, product, location, evaluation period, and horizon.

| Column | SQLite type | Required? | Key / constraint | Definition |
|---|---|---:|---|---|
| `forecast_evaluation_id` | INTEGER | Yes | PK | Internal evaluation-record identifier |
| `forecast_run_id` | INTEGER | Yes | FK → `forecast_runs.forecast_run_id` | Forecast run being evaluated |
| `product_id` | INTEGER | Yes | FK → `products.product_id` | Product evaluation scope |
| `location_id` | INTEGER | Yes | FK → `locations.location_id` | Location evaluation scope |
| `evaluation_start_date` | TEXT | Yes | ISO date | First date in evaluation period |
| `evaluation_end_date` | TEXT | Yes | ISO date; >= start | Last date in evaluation period |
| `horizon_days` | INTEGER | Yes | CHECK > 0 | Evaluated forecast horizon in days |
| `mae` | REAL | Yes | CHECK >= 0 and finite | Mean absolute error in demand units |
| `rmse` | REAL | Yes | CHECK >= 0 and finite | Root mean squared error in demand units |
| `wape` | REAL | No | CHECK >= 0 when non-NULL | WAPE stored as a non-negative ratio; may exceed 1 |
| `forecast_bias` | REAL | Yes | Finite | Mean forecast-minus-actual error |

Unique key: (`forecast_run_id`, `product_id`, `location_id`, `evaluation_start_date`, `evaluation_end_date`, `horizon_days`).

Rules:
- Evaluation end date must not precede evaluation start date.
- `mae` and `rmse` must be non-negative and finite.
- WAPE = `SUM(ABS(forecast - actual)) / SUM(actual)` over the evaluation scope.
- Store WAPE as a decimal ratio, not as a value from 0 to 100. WAPE may exceed 1.
- WAPE is `NULL` when total actual demand in the denominator is zero.
- Reports display WAPE as a percentage; for example, `0.125` is displayed as `12.5%`.
- Forecast bias is forecast minus actual; positive means overforecasting.
- Evaluation must use the agreed actual-demand measure and avoid temporal data leakage.
- The uniqueness key allows one evaluation record per defined scope. A new evaluation methodology/configuration that needs separate storage must be represented by a new forecast run or a documented schema revision.

## 14. Table: `planning_runs`

**Purpose:** Replenishment-planning execution or scenario metadata.  
**Grain:** One row per planning execution/scenario.

| Column | SQLite type | Required? | Key / constraint | Definition |
|---|---|---:|---|---|
| `planning_run_id` | INTEGER | Yes | PK | Internal planning-run identifier |
| `planning_date` | TEXT | Yes | ISO date | Effective date for inventory and supplier-term decisions |
| `scenario_name` | TEXT | Yes | Non-empty | Human-readable scenario name; repeated names allowed |
| `forecast_run_id` | INTEGER | No | FK → `forecast_runs.forecast_run_id` | Forecast run used by scenario, if any |
| `scenario_config` | TEXT | Yes | Valid JSON | Scenario assumptions, constraints, and planning parameters |
| `status` | TEXT | Yes | Controlled vocabulary | Planning-run lifecycle status |

Status values: `pending`, `running`, `completed`, `failed`.

Rules:
- Allowed transitions: `pending` → `running` → `completed` or `failed`.
- A failed run cannot become completed; retry with a new planning run.
- Every recommendation references a planning run.
- Scenario names are not unique; `planning_run_id` distinguishes executions.
- `scenario_config` must be valid JSON and include relevant horizon, service-level/safety-stock assumptions, and optimization constraints.
- Supplier terms are selected as valid on `planning_date`.

## 15. Table: `replenishment_recommendations`

**Purpose:** Replenishment decisions generated by a planning run.  
**Grain:** One recommendation per planning run, product, location, and supplier-product option.

| Column | SQLite type | Required? | Key / constraint | Definition |
|---|---|---:|---|---|
| `recommendation_id` | INTEGER | Yes | PK | Internal recommendation identifier |
| `planning_run_id` | INTEGER | Yes | FK → `planning_runs.planning_run_id` | Planning run that generated the recommendation |
| `product_id` | INTEGER | Yes | FK → `products.product_id` | Product requiring replenishment analysis |
| `location_id` | INTEGER | Yes | FK → `locations.location_id` | Location being replenished |
| `supplier_product_id` | INTEGER | Yes | FK → `supplier_products.supplier_product_id` | Supplier-product sourcing option |
| `recommendation_date` | TEXT | Yes | ISO date | Date recommendation was generated |
| `reorder_point` | INTEGER | Yes | CHECK >= 0 | Reorder threshold in base units |
| `recommended_qty` | INTEGER | Yes | CHECK >= 0 | Proposed order quantity in base units |
| `estimated_cost` | INTEGER | Yes | CHECK >= 0 | Estimated procurement cost in poisha |
| `status` | TEXT | Yes | Controlled vocabulary | Recommendation lifecycle status |

Unique key: (`planning_run_id`, `product_id`, `location_id`, `supplier_product_id`).

Status values and transitions:
- `proposed` → `approved`, `rejected`, or `cancelled`
- `approved` → `ordered` or `cancelled`
- `rejected`, `ordered`, and `cancelled` are terminal

Rules:
- The selected supplier-product option must supply the recommended product and be valid on the planning date.
- Positive recommendations must respect minimum order quantity and order multiples.
- A zero quantity means no additional order is suggested.
- Estimated cost must reconcile with recommended quantity and the effective supplier-specific purchase cost using documented rounding.
- Keep recommendation records when statuses change; do not delete rejected or cancelled recommendations.
- Forecast provenance is obtained through `planning_runs.forecast_run_id`; no `forecast_run_id` column exists in this table.
- Multiple supplier alternatives are allowed for the same product/location, but the composite uniqueness key prevents duplicates for the same supplier option within one planning run.

## 16. Relationship and foreign-key summary

| Child table | Foreign key | Parent table | Purpose |
|---|---|---|---|
| `supplier_products` | `supplier_id` | `suppliers` | Supplier offering the product |
| `supplier_products` | `product_id` | `products` | Product offered |
| `demand_history` | `product_id` | `products` | Demanded product |
| `demand_history` | `location_id` | `locations` | Demand location |
| `inventory_snapshots` | `product_id` | `products` | Stocked product |
| `inventory_snapshots` | `location_id` | `locations` | Stock location |
| `inventory_movements` | `product_id` | `products` | Moved product |
| `inventory_movements` | `location_id` | `locations` | Affected location |
| `purchase_orders` | `supplier_id` | `suppliers` | Supplier receiving order |
| `purchase_order_lines` | `purchase_order_id` | `purchase_orders` | Parent order |
| `purchase_order_lines` | `product_id` | `products` | Ordered product |
| `forecast_results` | `forecast_run_id` | `forecast_runs` | Generating forecast run |
| `forecast_results` | `product_id` | `products` | Forecast product |
| `forecast_results` | `location_id` | `locations` | Forecast location |
| `forecast_evaluations` | `forecast_run_id` | `forecast_runs` | Evaluated run |
| `forecast_evaluations` | `product_id` | `products` | Evaluation product |
| `forecast_evaluations` | `location_id` | `locations` | Evaluation location |
| `planning_runs` | `forecast_run_id` | `forecast_runs` | Optional informing forecast |
| `replenishment_recommendations` | `planning_run_id` | `planning_runs` | Generating planning run |
| `replenishment_recommendations` | `product_id` | `products` | Product being considered |
| `replenishment_recommendations` | `location_id` | `locations` | Replenishment location |
| `replenishment_recommendations` | `supplier_product_id` | `supplier_products` | Sourcing option |

All foreign keys use `ON DELETE RESTRICT ON UPDATE RESTRICT`; foreign-key enforcement must be enabled on every connection.

## 17. Data-quality and validation rules

### 17.1 Master data

- Product, location, and supplier codes must be unique and non-empty.
- Required names, category, region, and unit of measure must be non-empty.
- Boolean flags must be `0` or `1`.
- Costs and prices must be non-negative integer poisha.
- Supplier lead times must be non-negative when present.
- Supplier-product minimum quantity and order multiple must be positive.
- Supplier-product effective periods must be valid and non-overlapping for each supplier-product pair.

### 17.2 Demand data

- Demand must be unique per product-location-date.
- Quantity fields must be non-negative integers.
- Stockout flag must be `0` or `1`.
- Confirmed zero demand is stored explicitly; unknown dates are flagged rather than filled with zero.
- The synthetic generator creates complete daily product-location series.
- Revenue is fulfilled-sales revenue in integer poisha.

### 17.3 Inventory

- Inventory snapshots must be unique per product-location-timestamp.
- On-hand, reserved, on-order, and backorder quantities must be non-negative.
- Reserved quantity must not exceed on-hand quantity.
- Inventory position uses the formula documented in Section 1.3.
- Movement quantity deltas must be non-zero signed integers.
- Movement types must use the controlled vocabulary and sign mapping in Section 8; validate both in Python and tests.

### 17.4 Purchase orders

- Order and delivery dates must be valid ISO dates.
- Expected delivery date must not precede order date unless documented.
- Status transitions must follow Section 9.
- Ordered quantity must be positive; received and cancelled quantities must be non-negative and their sum must not exceed ordered quantity. Enforce the terminal-status reconciliation rules in Section 10.
- Draft and cancelled orders remain stored for auditability.
- Eligible outstanding order quantities must be calculated consistently when deriving on-order inventory.

### 17.5 Forecasts and evaluations

- Forecast windows and training dates must be valid.
- Forecast quantities and MAE/RMSE must be finite and non-negative. WAPE, when present, and forecast bias must be finite; validate all REAL inputs with Python finite-number checks before writing to SQLite.
- Forecast-result composite uniqueness must be enforced.
- Evaluation date range and horizon must be valid.
- WAPE is `NULL` when the total actual-demand denominator is zero.
- Evaluation uniqueness key must be enforced.
- Forecast/evaluation procedures must avoid temporal leakage.

### 17.6 Planning and recommendations

- Scenario configuration must be valid JSON.
- Planning status transitions must follow Section 14.
- Repeated scenario names are allowed.
- Recommendations must reference a valid planning run, product, location, and supplier-product option.
- The supplier-product row must match the recommendation's product and be valid on the planning date.
- Recommendation composite uniqueness and status transitions must be enforced.
- Quantity and estimated cost must reconcile with supplier constraints and effective supplier purchase cost.

## 18. Input validation and failure handling

- Missing input files, missing required columns, invalid schema, or critical invalid data must fail clearly with a useful error message.
- Recoverable row-level problems must be recorded with row identifiers and reasons; do not silently discard records.
- Historical records must not be physically deleted during normal processing.
- A failed run must not replace previously published outputs.
- Write multi-table changes in transactions and roll back incomplete writes.
- Publish new outputs only after processing and validation succeed.
- Keep error reports and run metadata sufficient to diagnose failures.

## 19. SQLite implementation requirements

When implementation is authorized:
1. Build tables from the approved ERD and this dictionary.
2. Add explicit primary keys, foreign keys, `NOT NULL`, `UNIQUE`, and `CHECK` constraints.
3. Use `ON DELETE RESTRICT ON UPDATE RESTRICT` for every foreign key.
4. Enable and verify `PRAGMA foreign_keys = ON` for every connection.
5. Add indexes based on expected query patterns and confirmed constraints.
6. Validate JSON configurations in Python.
7. Use transactions for multi-table changes.
8. Do not implement cascading deletion or routine physical deletion of historical business records.
9. Keep dataset provenance outside the 14-table baseline.
10. Do not create or migrate the physical database until both documents pass Section 22.

## 20. Dataset generation and provenance

Each generated dataset must have a corresponding metadata file, initially `data/processed/dataset_metadata.json`. Record:
- generator version;
- random seed;
- dataset configuration and generation parameters;
- relevant input/source identifiers when public data is used;
- compatible software/runtime assumptions where they affect reproducibility.

The seed and configuration must be sufficient to reproduce the generated data with the same generator version and compatible environment. Dataset provenance is outside the baseline 14-table SQLite schema.

## 21. Deferred scope

The following are explicitly deferred from the baseline schema and are not unresolved decisions for the current design:
- A separate audit-event table for every field change.
- Supplier delivery-performance history.
- Separate receipt-event tables for multiple deliveries against a purchase-order line.
- Line-level expected delivery dates and split-delivery scheduling.
- Multi-currency purchasing.
- Lot/batch, expiry-date, and serial-number inventory.
- Explicit transfer-header and transfer-line entities.
- Database-level enforcement for polymorphic inventory-movement references.

If any deferred item becomes necessary, revise both the ERD and this dictionary together before implementation.

## 22. Acceptance checklist

The data dictionary is ready for physical-schema implementation only when:
- [ ] Both documents list exactly the same 14 tables.
- [ ] Table and column names match `docs/database_erd.md`.
- [ ] Every column has matching type, requiredness, key, constraint, unit, and meaning in both documents.
- [ ] All primary keys, foreign keys, unique keys, and check constraints are specified.
- [ ] Every monetary field uses integer poisha.
- [ ] Demand, fulfilled sales, stockouts, and estimated lost demand are modeled separately.
- [ ] Confirmed zero-demand dates are distinct from missing/unknown dates.
- [ ] Inventory-position formula and inputs match the ERD.
- [ ] Supplier-product effective-date uniqueness and overlap validation are documented.
- [ ] Purchase-order, forecast-run, planning-run, and recommendation statuses/transitions match.
- [ ] Purchase-order received/cancelled quantities reconcile with ordered quantities and on-order calculations exclude cancelled quantities.
- [ ] Inventory movement type/sign rules are validated in Python and tests.
- [ ] All REAL inputs that require finite values are validated as finite in Python.
- [ ] Evaluation and recommendation uniqueness keys match.
- [ ] Foreign-key actions and historical-preservation policy match.
- [ ] Input failure, row-level issue reporting, and safe output publication are documented.
- [ ] Markdown headings, tables, and code spans render correctly.
- [ ] No unresolved schema decisions remain.
- [ ] The physical SQLite schema can be implemented from the approved documents without assumptions.

**End of document**
