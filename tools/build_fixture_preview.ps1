param(
    [string]$OutputPath = "docs/index.html"
)

$ErrorActionPreference = "Stop"
$scriptDirectory = if ($PSScriptRoot) { $PSScriptRoot } else { Join-Path (Get-Location).Path "tools" }
$projectRoot = Split-Path -Parent $scriptDirectory
$rendererPath = Join-Path $projectRoot "src/tw_market_report/render.py"
$renderer = Get-Content -LiteralPath $rendererPath -Raw -Encoding UTF8
$marker = "HTML = r'''"
$start = $renderer.IndexOf($marker)
if ($start -lt 0) { throw "Dashboard template marker not found" }
$start += $marker.Length
$finish = $renderer.IndexOf("'''", $start)
if ($finish -lt 0) { throw "Dashboard template terminator not found" }
$template = $renderer.Substring($start, $finish - $start)

$history = @()
for ($i = 0; $i -lt 20; $i++) {
    $history += [ordered]@{
        trade_date = (Get-Date "2026-06-30").AddDays($i).ToString("yyyy-MM-dd")
        composite_score = [math]::Round(47 + 8 * [math]::Sin($i / 3), 1)
        limit_up_count = [math]::Round(12 + 7 * [math]::Sin($i / 2))
        limit_down_count = [math]::Round(10 + 6 * [math]::Cos($i / 2.5))
        taiex_close = [math]::Round(22800 + $i * 34 + 180 * [math]::Sin($i / 3), 0)
    }
}

function Limit-Stats($market, $eligible, $up, $down, $upPct, $downPct, $upRank, $downRank) {
    return [ordered]@{
        market = $market
        eligible_count = $eligible
        limit_up_count = $up
        limit_down_count = $down
        intraday_up_touch_count = 13
        intraday_down_touch_count = 8
        limit_up_ratio = $up / $eligible
        limit_down_ratio = $down / $eligible
        limit_breadth = ($up - $down) / $eligible
        strength_ratio = ($up + 1) / ($down + 1)
        up_percentile_1y = $upPct
        down_percentile_1y = $downPct
        up_percentile_5y = $upPct - 3
        down_percentile_5y = $downPct - 2
        up_percentile_full = $upPct - 5
        down_percentile_full = $downPct - 4
        up_historical_rank = $upRank
        down_historical_rank = $downRank
        universe_verified = $true
        calculation_method = "fixture_official_per_security_limit_price"
    }
}

$payload = [ordered]@{
    trade_date = "2026-07-28"
    report_mode = "close"
    generated_at = "2026-07-28T22:00:00+08:00"
    domestic_market_state = "盤整"
    overnight_risk_state = "中性"
    composite_score = 53.7
    confidence = "中"
    model_exposure_range = @(40, 60)
    shadow_mode = $true
    reversal_stage = "初步反轉"
    reversal_reasons = @("跌停家數較壓力日收斂", "價格與一般廣度同步改善")
    module_scores = [ordered]@{
        trend_breadth = 56.4
        capital_flow = 48.8
        leverage_lending = 44.6
        futures = 52.1
        options_volatility = 58.3
        overseas = 61.5
        valuation = 47.9
    }
    module_coverage = [ordered]@{
        trend_breadth = 0.86
        capital_flow = 0.75
        leverage_lending = 0.75
        futures = 0.67
        options_volatility = 1.0
        overseas = 1.0
        valuation = 0.75
    }
    features = [ordered]@{
        taiex_close = 23412
        tsmc_close = 1165
        taiwan_vix = 24.8
        us_vix = 18.6
        futures_basis = -42
        futures_basis_pct = -0.00179
        put_call_volume_ratio = 1.07
        put_call_oi_ratio = 1.18
        sox_return_5d = 0.018
        tsm_adr_premium = -0.0015
        usd_twd = 30.41
        limit_scoring_enabled = $true
    }
    limits = [ordered]@{
        twse = Limit-Stats "twse" 1010 14 7 71 48 214 932
        tpex = Limit-Stats "tpex" 820 21 12 82 66 123 608
        combined = Limit-Stats "combined" 1830 35 19 78 58 167 741
    }
    historical_limit_analogs = @(
        [ordered]@{market="twse";trade_date="2025-11-21";limit_up_count=15;limit_down_count=7;return_1d=0.004;return_5d=0.018;return_10d=0.027;return_20d=0.041},
        [ordered]@{market="tpex";trade_date="2024-08-08";limit_up_count=22;limit_down_count=11;return_1d=-0.002;return_5d=0.011;return_10d=0.023;return_20d=0.032},
        [ordered]@{market="combined";trade_date="2023-10-31";limit_up_count=34;limit_down_count=20;return_1d=0.006;return_5d=0.015;return_10d=0.019;return_20d=0.037}
    )
    post_analog_returns = [ordered]@{}
    options_pressure_zones = [ordered]@{
        status = "fixture"
        calls = @([ordered]@{strike=23800;open_interest=28600}, [ordered]@{strike=24000;open_interest=25100})
        puts = @([ordered]@{strike=23000;open_interest=31200}, [ordered]@{strike=22800;open_interest=22300})
        max_pain = 23400
        pressure_balance = 0.08
    }
    positive_drivers = @("費半五日變化（+19.3）", "選擇權壓力平衡（+14.8）", "漲跌停淨廣度（+11.2）")
    negative_drivers = @("20日融資增幅（-13.6）", "外資買賣超占成交值（-8.7）")
    data_freshness = [ordered]@{
        "TWSE收盤行情" = "2026-07-28"
        "TPEx市場現況" = "2026-07-28"
        "TAIFEX期權" = "2026-07-28"
        "海外行情" = "2026-07-27"
    }
    source_status = @(
        [ordered]@{name="TWSE收盤行情";status="fixture";as_of="2026-07-28";message="離線示範資料";url=""},
        [ordered]@{name="TPEx市場現況";status="fixture";as_of="2026-07-28";message="離線示範資料";url=""},
        [ordered]@{name="TAIFEX期權";status="fixture";as_of="2026-07-28";message="離線示範資料";url=""},
        [ordered]@{name="海外行情";status="fixture";as_of="2026-07-27";message="離線示範資料";url=""}
    )
    history = $history
}

$json = $payload | ConvertTo-Json -Depth 20 -Compress
$html = $template.Replace("__DATA__", $json.Replace("</", "<\/"))
$target = Join-Path $projectRoot $OutputPath
$targetDirectory = Split-Path -Parent $target
New-Item -ItemType Directory -Path $targetDirectory -Force | Out-Null
[System.IO.File]::WriteAllText($target, $html, [System.Text.UTF8Encoding]::new($false))
Write-Output "Built fixture dashboard: $target"
