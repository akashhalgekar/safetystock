-- RAW LAYER
--
-- Every column is text deliberately. Raw must accept the source exactly as
-- sent: typing here would make the load reject malformed rows and we would
-- never learn they existed. Casting happens in staging, where a bad value
-- becomes an inspectable row rather than a load failure.
--
-- Column NAMES are canonical rather than source-specific, because this
-- pipeline is meant to run on anyone's extract. The source-to-canonical
-- mapping lives in one auditable file, mapping.yaml, so lineage stays
-- explicit. Values are untouched: nothing cleaned, nothing dropped, nothing
-- coerced.

DROP TABLE IF EXISTS raw.orders CASCADE;
CREATE TABLE raw.orders (
    order_id      text,   -- unique order / PO key
    status        text,
    ordered_at    text,   -- duration clock starts
    received_at   text,   -- duration clock stops
    promised_at   text,   -- the quoted / confirmed date
    dispatched_at text    -- optional, used only for a sanity check
);

DROP TABLE IF EXISTS raw.order_lines CASCADE;
CREATE TABLE raw.order_lines (
    order_id    text,
    line_no     text,
    product_id  text,
    supplier_id text,
    unit_value  text,    -- used for ABC ranking
    quantity    text     -- optional; NULL means one unit per line
);

DROP TABLE IF EXISTS raw.products CASCADE;
CREATE TABLE raw.products (
    product_id text,
    category   text      -- the grouping level for the demand grain
);

DROP TABLE IF EXISTS raw.suppliers CASCADE;
CREATE TABLE raw.suppliers (
    supplier_id text,
    city        text,
    region      text
);

DROP TABLE IF EXISTS raw.category_labels CASCADE;
CREATE TABLE raw.category_labels (
    category text,
    label    text
);
