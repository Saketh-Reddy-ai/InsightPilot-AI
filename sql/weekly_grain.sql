-- InsightPilot AI - weekly return grain
--
-- One row per week x traffic source x category, carrying COUNTS rather than
-- rates. Counts are additive, so every rate in the dashboard is derived at
-- display time from whatever slice the reader has filtered to. Storing rates
-- here would force the dashboard to average averages, which is wrong whenever
-- the segments differ in size.
--
-- Cancelled items are excluded: they never shipped, so they could never be
-- returned. Leaving them in the denominator drags every rate down by a few
-- tenths of a point and makes channels with high cancellation look better at
-- returning than they are.
--
-- 112 weeks = two full 52-week comparison windows plus headroom for the
-- return-lag guard, so the year-over-year comparison stays matched no matter
-- which week the schedule happens to run on.

SELECT
  DATE_TRUNC(DATE(oi.created_at), WEEK(MONDAY)) AS week_start,
  u.traffic_source                              AS traffic_source,
  p.category                                    AS category,
  COUNTIF(oi.status = 'Returned')               AS returned_items,
  COUNT(*)                                      AS total_items
FROM `bigquery-public-data.thelook_ecommerce.order_items` AS oi
JOIN `bigquery-public-data.thelook_ecommerce.users`    AS u ON u.id = oi.user_id
JOIN `bigquery-public-data.thelook_ecommerce.products` AS p ON p.id = oi.product_id
WHERE oi.status != 'Cancelled'
  AND p.category IS NOT NULL
  AND u.traffic_source IS NOT NULL
  AND DATE(oi.created_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL 112 WEEK)
  AND DATE(oi.created_at) <  CURRENT_DATE()
GROUP BY week_start, traffic_source, category
ORDER BY week_start, traffic_source, category
