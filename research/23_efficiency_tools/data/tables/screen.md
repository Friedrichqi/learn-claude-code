# Topic 23 prompt screens (et_screen.py; rules: screen_prereg.md, screen_rules.json)

## Agent replies (work stratum; item means, 95 % session-cluster bootstrap)

| condition | items | wait | save_context | save with wait | wait-only | kept |
|---|---|---|---|---|---|---|
| B0 | 42 | 38 % [25, 51] | 0 % [0, 0] | 0 % [0, 0] | 1 % [0, 4] | 92 % [87, 97] |
| S1@62 | 42 | 3 % [1, 6] | 1 % [0, 2] | 0 % [0, 0] | 0 % [0, 0] | 92 % [85, 98] |
| S1@93 | 42 | 61 % [46, 76] | 19 % [10, 29] | 18 % [9, 28] | 5 % [1, 11] | 84 % [74, 92] |
| S2@62 | 42 | 3 % [0, 7] | 0 % [0, 0] | 0 % [0, 0] | 0 % [0, 0] | 92 % [85, 98] |
| S2@93 | 42 | 64 % [50, 78] | 0 % [0, 0] | 0 % [0, 0] | 4 % [1, 8] | 86 % [77, 95] |
| S3@62 | 42 | 4 % [1, 7] | 0 % [0, 0] | 0 % [0, 0] | 0 % [0, 0] | 89 % [83, 95] |
| S3@93 | 42 | 61 % [47, 75] | 6 % [1, 12] | 6 % [1, 12] | 3 % [0, 7] | 86 % [77, 94] |
| S4@62 | 42 | 4 % [1, 7] | 0 % [0, 0] | 0 % [0, 0] | 0 % [0, 0] | 90 % [81, 98] |
| S4@93 | 42 | 60 % [46, 73] | 4 % [1, 8] | 3 % [0, 7] | 4 % [1, 8] | 89 % [79, 96] |
| S5@62 | 42 | 4 % [0, 8] | 0 % [0, 0] | 0 % [0, 0] | 0 % [0, 0] | 92 % [86, 98] |
| S5@93 | 42 | 77 % [71, 84] | 73 % [65, 81] | 73 % [65, 81] | 7 % [2, 11] | 87 % [78, 93] |
| S6@62 | 42 | 3 % [0, 7] | 0 % [0, 0] | 0 % [0, 0] | 0 % [0, 0] | 88 % [80, 96] |
| S6@93 | 42 | 58 % [45, 71] | 17 % [8, 27] | 17 % [8, 27] | 2 % [0, 6] | 87 % [78, 95] |
| W@62 | 42 | 5 % [2, 9] | 0 % [0, 0] | 0 % [0, 0] | 0 % [0, 0] | 91 % [84, 97] |
| W@93 | 42 | 63 % [49, 76] | 0 % [0, 0] | 0 % [0, 0] | 2 % [0, 4] | 86 % [77, 94] |

## Controller (direction of the chosen K against the reference rule)

| variant | effort | calls | valid | agree | agree when lower | agree when raise | agree when keep |
|---|---|---|---|---|---|---|---|
| c1 | default | 336 | 91 % | 97 % | 97 % | 100 % | 62 % |
| c1 | low | 336 | 100 % | 96 % | 98 % | 99 % | 53 % |
| c2 | default | 336 | 85 % | 97 % | 99 % | 98 % | 50 % |
| c2 | low | 336 | 100 % | 94 % | 100 % | 93 % | 40 % |
| c3 | default | 336 | 95 % | 98 % | 100 % | 100 % | 67 % |
| c3 | low | 336 | 100 % | 98 % | 100 % | 99 % | 60 % |
| c4 | default | 336 | 98 % | 98 % | 99 % | 99 % | 77 % |
| c4 | low | 336 | 100 % | 98 % | 100 % | 99 % | 60 % |

## Winners by the pre-registered rules

```
{
 "wait_v5": {
  "high": 0.6309523809523809,
  "low": 0.05357142857142857,
  "passes": true
 },
 "save_s1": {
  "save_with_wait_high": 0.18452380952380953,
  "save_low": 0.005952380952380952,
  "wait_only_rise": 0.03571428571428571,
  "passes": false
 },
 "save_s2": {
  "save_with_wait_high": 0.0,
  "save_low": 0.0,
  "wait_only_rise": 0.023809523809523808,
  "passes": false
 },
 "save_s3": {
  "save_with_wait_high": 0.05952380952380952,
  "save_low": 0.0,
  "wait_only_rise": 0.011904761904761904,
  "passes": false
 },
 "save_s4": {
  "save_with_wait_high": 0.02976190476190476,
  "save_low": 0.0,
  "wait_only_rise": 0.023809523809523808,
  "passes": false
 },
 "save_s5": {
  "save_with_wait_high": 0.7321428571428571,
  "save_low": 0.0,
  "wait_only_rise": 0.04761904761904762,
  "passes": true
 },
 "save_s6": {
  "save_with_wait_high": 0.16666666666666666,
  "save_low": 0.0,
  "wait_only_rise": 0.005952380952380952,
  "passes": false
 },
 "save_winner": "s5",
 "controller_c1|default": {
  "calls": 336,
  "valid": 0.9107142857142857,
  "agree": 0.9705882352941176,
  "lower": 0.9741935483870968,
  "raise": 1.0,
  "keep": 0.6153846153846154,
  "passes": false
 },
 "controller_c1|low": {
  "calls": 336,
  "valid": 1.0,
  "agree": 0.9642857142857143,
  "lower": 0.9774011299435028,
  "raise": 0.9930555555555556,
  "keep": 0.5333333333333333,
  "passes": true
 },
 "controller_c2|default": {
  "calls": 336,
  "valid": 0.8541666666666666,
  "agree": 0.9686411149825784,
  "lower": 0.9867549668874173,
  "raise": 0.9841269841269841,
  "keep": 0.5,
  "passes": false
 },
 "controller_c2|low": {
  "calls": 336,
  "valid": 1.0,
  "agree": 0.9434523809523809,
  "lower": 1.0,
  "raise": 0.9305555555555556,
  "keep": 0.4,
  "passes": true
 },
 "controller_c3|default": {
  "calls": 336,
  "valid": 0.9464285714285714,
  "agree": 0.9842767295597484,
  "lower": 1.0,
  "raise": 1.0,
  "keep": 0.6666666666666666,
  "passes": false
 },
 "controller_c3|low": {
  "calls": 336,
  "valid": 1.0,
  "agree": 0.9761904761904762,
  "lower": 1.0,
  "raise": 0.9861111111111112,
  "keep": 0.6,
  "passes": true
 },
 "controller_c4|default": {
  "calls": 336,
  "valid": 0.9821428571428571,
  "agree": 0.9848484848484849,
  "lower": 0.9942528735632183,
  "raise": 0.993006993006993,
  "keep": 0.7692307692307693,
  "passes": true
 },
 "controller_c4|low": {
  "calls": 336,
  "valid": 1.0,
  "agree": 0.9761904761904762,
  "lower": 1.0,
  "raise": 0.9861111111111112,
  "keep": 0.6,
  "passes": true
 },
 "controller_winner": "c4|default"
}
```
