# Discovered source schemas

Source CSVs are preserved unchanged. Amounts are USD. Empty fields are nullable.

## ground_truth.csv — 20,000 rows

`claim_id`, `provider_id`, `scenario_type`, `injected_suspicious_pattern`, `scenario_group_id`, `scenario_date`, `synthetic_exposure_proxy_usd`, `future_30d_observable`, `future_30d_injected_pattern`, `future_60d_observable`, `future_60d_injected_pattern`, `future_90d_observable`, `future_90d_injected_pattern`

## billing_policies.csv — 10 rows

`policy_id`, `rule_type`, `applicable_claim_types`, `synthetic_rule_description`, `effective_from`, `effective_to`, `limitation`, `policy_origin`

## claim_estimates.csv — 1,850 rows

`estimate_id`, `claim_id`, `episode_id`, `estimate_date`, `estimated_billed_usd`, `estimate_scope`, `final_billed_usd`, `difference_usd`

## claim_lines.csv — 27,221 rows

`claim_line_id`, `claim_id`, `procedure_code`, `unit_count`, `unit_charge_usd`, `line_billed_usd`, `modifier`, `days_supply`, `days_since_last_fill`, `line_role`

## claims.csv — 20,000 rows

`claim_id`, `member_id`, `provider_id`, `facility_id`, `encounter_id`, `service_date`, `service_start`, `service_duration_min`, `claim_type`, `claim_status`, `submitted_date`, `payment_date`, `primary_code`, `correction_of_claim_id`, `related_claim_id`, `complexity_band`, `billed_amount_usd`, `allowed_amount_usd`, `paid_amount_usd`, `member_responsibility_usd`

## encounters.csv — 19,705 rows

`encounter_id`, `member_id`, `provider_id`, `facility_id`, `service_start`, `duration_min`, `documented_code`, `documented_complexity`, `encounter_source`

## facilities.csv — 60 rows

`facility_id`, `facility_name`, `facility_type`, `state`, `city`, `owner_id`, `complexity_mix`

## investigation_history.csv — 150 rows

`investigation_id`, `provider_id`, `facility_id`, `opened_date`, `closed_date`, `outcome`, `outcome_available_date`, `review_channel`, `case_summary`

## members.csv — 4,000 rows

`member_id`, `age_band`, `plan_type`, `state`, `city`, `complexity_band`, `synthetic_identity`

## providers.csv — 250 rows

`provider_id`, `provider_name`, `provider_type`, `specialty`, `facility_id`, `state`, `active_from`, `synthetic_identity`

## referrals.csv — 2,000 rows

`referral_id`, `source_provider_id`, `destination_provider_id`, `destination_facility_id`, `member_id`, `referral_date`, `referral_reason`, `referral_source`

## relationships.csv — 410 rows

`relationship_id`, `source_id`, `source_type`, `target_id`, `target_type`, `relationship_type`, `effective_from`, `effective_to`, `record_source`, `verification_status`

## supply_items.csv — 7,081 rows

`supply_id`, `claim_id`, `claim_line_id`, `supply_code`, `supply_description`, `quantity`, `unit_charge_usd`, `line_billed_usd`, `billing_basis`
