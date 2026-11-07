-- InsightPilot AI - how long returns actually take
--
-- The most recent weeks in any returns dataset look great, because the items
-- sold in them have not come back yet. Comparing a censored recent window
-- against a settled one invents an improvement that is not there.
--
-- So measure the lag instead of guessing it. The 90th percentile is the point
-- where almost every return has landed; the refresh script converts it to a
-- whole number of weeks and drops that many from BOTH comparison windows,
-- keeping them matched. Measuring beats a magic number in a comment, because
-- the guard then stays correct as the data changes underneath it.
--
-- Scans two columns of one table, so it is close to free.

SELECT APPROX_QUANTILES(
         DATE_DIFF(DATE(returned_at), DATE(created_at), DAY), 100
       )[OFFSET(90)] AS p90_days
FROM `bigquery-public-data.thelook_ecommerce.order_items`
WHERE returned_at IS NOT NULL
  -- A handful of rows carry a return date before the order date. They are data
  -- entry noise, and a negative lag would drag the percentile down.
  AND returned_at >= created_at
