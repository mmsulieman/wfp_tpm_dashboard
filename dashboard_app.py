# dashboard_app.py
import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import pydeck as pdk
import io
import datetime as dt
import fitz  # pymupdf
import base64
import requests

st.set_page_config(page_title="WFP–TPM Monitoring Dashboard", layout="wide")
st.title("WFP–TPM Monitoring Dashboard")
st.caption("Somali Region | Allocation + KPIs + Seaborn Charts + Map + Excel/PDF + Flow + Rosters")

# Seaborn theme
sns.set_theme(style="whitegrid", context="notebook")

# Sidebar settings
st.sidebar.header("Scenario Settings")
seed = st.sidebar.number_input("Random seed", min_value=0, value=42, step=1)
np.random.seed(seed)

wfp_share = st.sidebar.slider("WFP % (non‑refugee activities)", 0, 100, 30, 1)
act3_spots = st.sidebar.number_input("Random WFP sites per Act 3 Woreda (overnight)", min_value=0, value=2, step=1)

scope = st.sidebar.selectbox("Enforce WFP% by", ["Overall (non‑refugee)", "Per Woreda (non‑refugee)", "Per Woreda × Activity (non‑refugee)"])

st.sidebar.header("Alert thresholds")
red_thr = st.sidebar.slider("RED if WFP % below", 0, 100, 20)
yellow_thr = st.sidebar.slider("YELLOW if WFP % below", 0, 100, 30)

st.sidebar.header("Share / Automate")
flow_url = st.sidebar.text_input("Power Automate webhook URL (optional)")
flow_payload_mode = st.sidebar.selectbox("Send PDF as", ["binary", "base64"])

st.sidebar.header("Monitor Rosters (optional)")
use_rosters = st.sidebar.checkbox("Enable named monitors & capacity", value=True)
prefer_base = st.sidebar.checkbox("Prefer base Woreda when assigning", value=True)

roster_info = st.sidebar.radio("Roster input", ["Single combined file","Separate WFP & TPM files"], index=0)
if roster_info == "Single combined file":
    roster_file = st.sidebar.file_uploader("Upload roster CSV/XLSX (columns: monitor_name, org, capacity, base_woreda)", type=["csv","xlsx"])
    wfp_roster_file = None
    tpm_roster_file = None
else:
    wfp_roster_file = st.sidebar.file_uploader("Upload WFP roster CSV/XLSX", type=["csv","xlsx"], key="wfp_roster")
    tpm_roster_file = st.sidebar.file_uploader("Upload TPM roster CSV/XLSX", type=["csv","xlsx"], key="tpm_roster")
    roster_file = None

# Upload activity plan
st.subheader("1) Upload activity plan")
upload = st.file_uploader("Upload activity plan (CSV or Excel)", type=["csv","xlsx"])

@st.cache_data
def sample_data():
    data = [
        ["Jijiga", "Camp E1", "Act3", "Refugees", 8, 9.345, 42.787, "ARRA"],
        ["Jijiga", "Camp E2", "Act3", "Refugees", 6, 9.355, 42.792, "ARRA"],
        ["Awbare", "Site A1", "Act1", "Relief", 4, 9.655, 43.025, "DRMC"],
        ["Awbare", "Site A2", "Act1", "Relief", 3, 9.662, 43.012, "DRMC"],
        ["Shinile", "HC S1", "Act2", "Nutrition", 5, 9.195, 41.860, "RHB"],
        ["Shinile", "HC S2", "Act2", "Nutrition", 4, 9.205, 41.870, "RHB"],
        ["Kebri Beyah", "WS K1", "Act5", "Climate", 3, 9.600, 43.200, "BOM"],
        ["Kebri Beyah", "WS K2", "Act5", "Climate", 2, 9.590, 43.210, "BOM"],
    ]
    return pd.DataFrame(data, columns=[
        "Woreda","Distribution Site","Activity Code","Activity Name","Total Planned Sites","Latitude","Longitude","Partner"
    ])

if upload is None:
    df = sample_data()
    st.info("Using sample activity data. Upload your own to override.")
else:
    df = pd.read_csv(upload) if upload.name.lower().endswith(".csv") else pd.read_excel(upload)

required = ["Woreda","Distribution Site","Activity Code","Activity Name","Total Planned Sites","Latitude","Longitude"]
missing = [c for c in required if c not in df.columns]
if missing:
    st.error(f"Missing required columns in activity plan: {missing}")
    st.stop()

# Partner column normalization
partner_col = None
for cand in ["Partner","Implementing Partner","IP","CP"]:
    if cand in df.columns:
        partner_col = cand
        break
if partner_col is None:
    df["Partner"] = "(none)"
else:
    df.rename(columns={partner_col: "Partner"}, inplace=True)

for c in ["Total Planned Sites","Latitude","Longitude"]:
    df[c] = pd.to_numeric(df[c], errors='coerce')

df = df.dropna(subset=["Latitude","Longitude"]).copy()

# Filters
st.subheader("2) Allocation & Filters")
fc1, fc2, fc3, fc4 = st.columns(4)
with fc1:
    act_filter = st.multiselect("Filter Activity", sorted(df["Activity Code"].unique().tolist()))
with fc2:
    wor_filter = st.multiselect("Filter Woreda", sorted(df["Woreda"].unique().tolist()))
with fc3:
    site_filter = st.multiselect("Filter Site", sorted(df["Distribution Site"].unique().tolist()))
with fc4:
    partner_filter = st.multiselect("Filter Partner", sorted(df["Partner"].unique().tolist()))

flt = df.copy()
if act_filter:
    flt = flt[flt["Activity Code"].isin(act_filter)]
if wor_filter:
    flt = flt[flt["Woreda"].isin(wor_filter)]
if site_filter:
    flt = flt[flt["Distribution Site"].isin(site_filter)]
if partner_filter:
    flt = flt[flt["Partner"].isin(partner_filter)]

# Expand to allocation units
units = []
for _, r in flt.iterrows():
    n = int(max(r["Total Planned Sites"], 0)) if not pd.isna(r["Total Planned Sites"]) else 0
    for i in range(n):
        units.append({
            "Woreda": r["Woreda"],
            "Distribution Site": r["Distribution Site"],
            "Activity Code": r["Activity Code"],
            "Activity Name": r["Activity Name"],
            "Latitude": r["Latitude"],
            "Longitude": r["Longitude"],
            "Partner": r["Partner"],
            "Modality": "routine",
        })
exp = pd.DataFrame(units)

if exp.empty:
    st.warning("No rows to allocate (check 'Total Planned Sites' and filters).")
    st.stop()

# Allocation logic
exp["Assigned To"] = "TPM"
non_ref = exp[exp["Activity Code"] != "Act3"].copy()

if scope == "Overall (non‑refugee)":
    idx = non_ref.index.to_numpy()
    k = int(np.round(len(idx) * (wfp_share/100.0)))
    chosen = set(np.random.choice(idx, size=k, replace=False)) if k>0 else set()
    exp.loc[non_ref.index, "Assigned To"] = np.where(non_ref.index.isin(chosen), "WFP", "TPM")
elif scope == "Per Woreda (non‑refugee)":
    for wor, sub in non_ref.groupby("Woreda"):
        idx = sub.index.to_numpy()
        k = int(np.round(len(idx) * (wfp_share/100.0)))
        chosen = set(np.random.choice(idx, size=k, replace=False)) if k>0 else set()
        exp.loc[sub.index, "Assigned To"] = np.where(sub.index.isin(chosen), "WFP", "TPM")
else:
    for (wor, act), sub in non_ref.groupby(["Woreda","Activity Code"]):
        idx = sub.index.to_numpy()
        k = int(np.round(len(idx) * (wfp_share/100.0)))
        chosen = set(np.random.choice(idx, size=k, replace=False)) if k>0 else set()
        exp.loc[sub.index, "Assigned To"] = np.where(sub.index.isin(chosen), "WFP", "TPM")

# Act3 routine -> TPM
exp.loc[exp["Activity Code"]=="Act3", "Assigned To"] = "TPM"

# Act3 overnights (WFP) per Woreda
spot_rows = []
if act3_spots > 0:
    for wor, sub in exp[exp["Activity Code"]=="Act3"].groupby("Woreda"):
        if len(sub) == 0:
            continue
        picks = sub.sample(n=min(act3_spots, len(sub)), random_state=np.random.randint(0, 1_000_000))
        s = picks.copy()
        s["Assigned To"] = "WFP"
        s["Modality"] = "overnight_spot"
        spot_rows.append(s)
if spot_rows:
    exp = pd.concat([exp] + spot_rows, ignore_index=True)

# Rosters
monitor_load = None
if use_rosters:
    def read_df(file):
        if file is None:
            return None
        if file.name.lower().endswith('.csv'):
            return pd.read_csv(file)
        return pd.read_excel(file)

    if roster_info == "Single combined file":
        combined = read_df(roster_file)
        if combined is None:
            combined = pd.DataFrame([
                {"monitor_name":"WFP-1","org":"WFP","capacity":25,"base_woreda":"Jijiga"},
                {"monitor_name":"WFP-2","org":"WFP","capacity":25,"base_woreda":"Awbare"},
                {"monitor_name":"TPM-1","org":"TPM","capacity":40,"base_woreda":"Awbare"},
                {"monitor_name":"TPM-2","org":"TPM","capacity":40,"base_woreda":"Kebri Beyah"},
            ])
            st.info("Using sample combined roster (upload to override).")
        rename = {c:c.lower() for c in combined.columns}
        combined = combined.rename(columns=rename)
        if "base_woreda" not in combined.columns:
            combined["base_woreda"] = "(any)"
        roster_wfp = combined[combined["org"].str.upper()=="WFP"][["monitor_name","capacity","base_woreda"]].copy()
        roster_tpm = combined[combined["org"].str.upper()=="TPM"]["monitor_name"].to_frame()
        roster_tpm["capacity"] = combined[combined["org"].str.upper()=="TPM"]["capacity"].values
        roster_tpm["base_woreda"] = combined[combined["org"].str.upper()=="TPM"]["base_woreda"].values
    else:
        roster_wfp = pd.DataFrame([
            {"monitor_name":"WFP-1","capacity":25,"base_woreda":"Jijiga"},
            {"monitor_name":"WFP-2","capacity":25,"base_woreda":"Awbare"},
        ])
        roster_tpm = pd.DataFrame([
            {"monitor_name":"TPM-1","capacity":40,"base_woreda":"Awbare"},
            {"monitor_name":"TPM-2","capacity":40,"base_woreda":"Kebri Beyah"},
        ])

    for r in (roster_wfp, roster_tpm):
        r["capacity"] = pd.to_numeric(r["capacity"], errors='coerce').fillna(0).astype(int)
        r["base_woreda"] = r["base_woreda"].astype(str)

    def assign_monitors(df_units: pd.DataFrame, roster: pd.DataFrame, prefer=True) -> pd.Series:
        if df_units.empty:
            return pd.Series(index=df_units.index, dtype=object)
        remaining = roster.set_index("monitor_name")["capacity"].to_dict()
        base_map = roster.set_index("monitor_name")["base_woreda"].to_dict()
        monitors_all = list(remaining.keys())
        def cycle_for_woreda(w):
            if not prefer:
                return monitors_all
            pref = [m for m in monitors_all if str(base_map.get(m,"(any)")).lower()==str(w).lower()]
            if not pref:
                pref = [m for m in monitors_all if str(base_map.get(m,"(any)")).lower()=="(any)"]
            return pref + [m for m in monitors_all if m not in pref]
        assignment = pd.Series(index=df_units.index, dtype=object)
        for wor, sub in df_units.sort_values(["Woreda","Activity Code","Distribution Site"]).groupby("Woreda"):
            order = cycle_for_woreda(wor)
            pos = 0
            for idx in sub.index:
                attempts = 0
                chosen = None
                while attempts < len(order):
                    m = order[pos % len(order)]
                    if remaining.get(m,0) > 0:
                        chosen = m
                        break
                    pos += 1
                    attempts += 1
                if chosen is None:
                    chosen = order[pos % len(order)]
                assignment.at[idx] = chosen
                remaining[chosen] = remaining.get(chosen,0) - 1
                pos += 1
        return assignment

    exp["Monitor Name"] = None
    routine_mask = exp["Modality"]=="routine"
    exp.loc[routine_mask & (exp["Assigned To"]=="WFP"), "Monitor Name"] = assign_monitors(exp.loc[routine_mask & (exp["Assigned To"]=="WFP")], roster_wfp, prefer_base)
    exp.loc[routine_mask & (exp["Assigned To"]=="TPM"), "Monitor Name"] = assign_monitors(exp.loc[routine_mask & (exp["Assigned To"]=="TPM")], roster_tpm, prefer_base)

    load = (exp[routine_mask].groupby(["Assigned To","Monitor Name"]).size().reset_index(name="assigned_units"))
    roster_full = pd.concat([
        roster_wfp.assign(**{"Assigned To":"WFP"}).rename(columns={"monitor_name":"Monitor Name"}),
        roster_tpm.assign(**{"Assigned To":"TPM"}).rename(columns={"monitor_name":"Monitor Name"})
    ], ignore_index=True)
    monitor_load = roster_full.merge(load, on=["Assigned To","Monitor Name"], how="left").fillna({"assigned_units":0})
    monitor_load["over_by"] = monitor_load["assigned_units"] - monitor_load["capacity"]
    monitor_load["over_capacity"] = monitor_load["over_by"] > 0
    if monitor_load["over_capacity"].any():
        st.warning("Some monitors exceed capacity.")

# KPIs (routine)
routine = exp[exp["Modality"]=="routine"].copy()
total_units = len(routine)
wfp_units = (routine["Assigned To"]=="WFP").sum()
tpm_units = (routine["Assigned To"]=="TPM").sum()
wfp_pct = (wfp_units/total_units*100) if total_units>0 else 0
tpm_pct = (tpm_units/total_units*100) if total_units>0 else 0

if wfp_pct < red_thr:
    alert = ("RED", "#D9534F")
elif wfp_pct < yellow_thr:
    alert = ("YELLOW", "#F0AD4E")
else:
    alert = ("GREEN", "#5CB85C")

kc1, kc2, kc3, kc4 = st.columns(4)
kc1.metric("Total routine units", f"{total_units:,}")
kc2.metric("WFP (units / %)", f"{wfp_units:,}", f"{wfp_pct:.1f}%")
kc3.metric("TPM (units / %)", f"{tpm_units:,}", f"{tpm_pct:.1f}%")
with kc4:
    st.markdown(f"**Alert:** <span style='color:{alert[1]};font-size:1.5em'>**{alert[0]}**</span>", unsafe_allow_html=True)

# Coverage table
st.subheader("3) Coverage Table (with alerts)")
with st.expander("Table settings", expanded=False):
    by_partner = st.checkbox("Break down by Partner", value=False)

cov_dims = ["Woreda","Activity Code"] + (["Partner"] if by_partner else [])

cov = (routine.groupby(cov_dims)['Assigned To'].value_counts().unstack(fill_value=0).reset_index())
if 'WFP' not in cov.columns: cov['WFP'] = 0
if 'TPM' not in cov.columns: cov['TPM'] = 0
cov['Total'] = cov['WFP'] + cov['TPM']
cov['WFP %'] = np.where(cov['Total']>0, cov['WFP']/cov['Total']*100, 0)

def row_alert(p):
    if p < red_thr: return 'RED'
    if p < yellow_thr: return 'YELLOW'
    return 'GREEN'

cov['Alert'] = cov['WFP %'].apply(row_alert)

st.dataframe(cov)

# Charts (Seaborn)
st.subheader("4) Charts")
labels = cov.apply(lambda r: " | ".join([str(r[c]) for c in cov_dims]), axis=1)
bar_df = cov.copy(); bar_df['label'] = labels

fig1, ax1 = plt.subplots(figsize=(max(8, len(labels)*0.5), 4))
long_df = bar_df.melt(id_vars='label', value_vars=['WFP','TPM'], var_name='Org', value_name='Units')
sns.barplot(data=long_df, x='label', y='Units', hue='Org', palette={'WFP':'#2E86C1','TPM':'#58D68D'}, ax=ax1)
ax1.set_xlabel(''); ax1.set_ylabel('Units')
ax1.set_title('WFP vs TPM by ' + ' × '.join(cov_dims))
ax1.tick_params(axis='x', rotation=45); ax1.legend(title='')
plt.tight_layout()

st.pyplot(fig1)

fig2, ax2 = plt.subplots(figsize=(4,4))
ax2.pie([wfp_units, tpm_units], labels=[f"WFP ({wfp_units})", f"TPM ({tpm_units})"], autopct='%1.1f%%', colors=sns.color_palette(['#2E86C1','#58D68D']))
ax2.set_title('Overall Allocation (routine)')
plt.tight_layout()

st.pyplot(fig2)

# Map
st.subheader("5) Map of Distribution Sites")
site_cov = (routine.groupby(["Woreda","Distribution Site","Latitude","Longitude"])['Assigned To']
                 .value_counts().unstack(fill_value=0).reset_index())
if 'WFP' not in site_cov.columns: site_cov['WFP']=0
if 'TPM' not in site_cov.columns: site_cov['TPM']=0
site_cov['Total'] = site_cov['WFP'] + site_cov['TPM']
site_cov['TPM %'] = np.where(site_cov['Total']>0, site_cov['TPM']/site_cov['Total']*100, 0)
site_cov['radius'] = 200 + 20*site_cov['Total']
site_cov['color_r'] = (255 * (100 - site_cov['TPM %'])/100).astype(int)
site_cov['color_g'] = (255 * (site_cov['TPM %'])/100).astype(int)
site_cov['color_b'] = 80

midpoint = (np.mean(site_cov['Latitude']), np.mean(site_cov['Longitude'])) if len(site_cov)>0 else (9.35, 42.79)
layer = pdk.Layer('ScatterplotLayer', data=site_cov, get_position='[Longitude, Latitude]', get_radius='radius', get_fill_color='[color_r, color_g, color_b, 180]', pickable=True)

tooltip = {"html": "<b>{Distribution Site}</b><br/>Woreda: {Woreda}<br/>TPM %: {TPM %}<br/>Total: {Total}", "style": {"backgroundColor": "steelblue", "color": "white"}}
view_state = pdk.ViewState(latitude=midpoint[0], longitude=midpoint[1], zoom=7)
st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=view_state, tooltip=tooltip))

# Excel export
st.subheader("6) Export Data (Excel)")

def to_excel_bytes(**dfs):
    from io import BytesIO
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for sheet, d in dfs.items():
            if d is None or (hasattr(d, 'empty') and d.empty):
                continue
            d.to_excel(writer, index=False, sheet_name=sheet[:31])
    return output.getvalue()

alloc_cols = ["Woreda","Distribution Site","Activity Code","Activity Name","Partner","Latitude","Longitude","Assigned To","Modality","Monitor Name"]
try:
    allocations_export = exp[alloc_cols].copy()
except Exception:
    allocations_export = exp.copy()

excel_bytes = to_excel_bytes(allocations=allocations_export, coverage=cov, monitor_load=(monitor_load if monitor_load is not None else None))

st.download_button("⬇️ Download Excel (allocations, coverage, monitor_load)", data=excel_bytes, file_name=f"wfp_tpm_dashboard_export_{dt.date.today().isoformat()}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# PDF export
st.subheader("7) Export PDF & Share")
colpdf1, colpdf2 = st.columns([2,1])
with colpdf1:
    pdf_name = st.text_input("PDF file name", value=f"wfp_tpm_dashboard_{dt.date.today().isoformat()}.pdf")
with colpdf2:
    gen_pdf = st.button("Generate PDF", type="primary")

if gen_pdf:
    def fig_to_png_bytes(fig):
        buf = io.BytesIO(); fig.savefig(buf, format='png', dpi=180, bbox_inches='tight'); buf.seek(0); return buf.read()
    png1 = fig_to_png_bytes(fig1); png2 = fig_to_png_bytes(fig2)

    # Coverage table image
    tbl_fig, tbl_ax = plt.subplots(figsize=(10, max(3, 0.3*len(cov))))
    tbl_ax.axis('off')
    tbl = tbl_ax.table(cellText=cov.round({'WFP %':1}).values, colLabels=cov.columns, loc='center')
    tbl.auto_set_font_size(False); tbl.set_fontsize(8); tbl.scale(1, 1.2)
    tbl_buf = io.BytesIO(); tbl_fig.savefig(tbl_buf, format='png', dpi=180, bbox_inches='tight'); plt.close(tbl_fig); tbl_buf.seek(0)
    png_tbl = tbl_buf.read()

    # Monitor load image
    png_load = None
    if monitor_load is not None and len(monitor_load)>0:
        load_fig, load_ax = plt.subplots(figsize=(10, max(3, 0.25*len(monitor_load))))
        load_ax.axis('off')
        cols = ["Assigned To","Monitor Name","base_woreda","capacity","assigned_units","over_by","over_capacity"]
        show = monitor_load[cols]
        tbl2 = load_ax.table(cellText=show.values, colLabels=show.columns, loc='center')
        tbl2.auto_set_font_size(False); tbl2.set_fontsize(8); tbl2.scale(1, 1.1)
        load_buf = io.BytesIO(); load_fig.savefig(load_buf, format='png', dpi=180, bbox_inches='tight'); plt.close(load_fig); load_buf.seek(0)
        png_load = load_buf.read()

    # PDF compose
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    title = "WFP–TPM Dashboard Report"; subtitle = f"Generated: {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}"
    kpi_text = f"Total units: {total_units}\nWFP: {wfp_units} ({wfp_pct:.1f}%)\nTPM: {tpm_units} ({tpm_pct:.1f}%)\nAlert: {alert[0]}"
    page.insert_text((40,50), title, fontsize=18, fontname="helv", fill=(0,0,0))
    page.insert_text((40,75), subtitle, fontsize=10, fontname="helv")
    page.insert_text((40,110), "KPIs", fontsize=14, fontname="helv")
    page.insert_text((40,130), kpi_text, fontsize=11, fontname="helv")
    page.insert_image(fitz.Rect(40, 170, 555, 400), stream=png1)
    page.insert_image(fitz.Rect(320, 410, 555, 650), stream=png2)

    page2 = doc.new_page(width=595, height=842)
    page2.insert_text((40,40), "Coverage Table", fontsize=14, fontname="helv")
    page2.insert_image(fitz.Rect(40, 60, 555, 780), stream=png_tbl)

    if png_load is not None:
        page3 = doc.new_page(width=595, height=842)
        page3.insert_text((40,40), "Monitor Load & Capacity", fontsize=14, fontname="helv")
        page3.insert_image(fitz.Rect(40, 60, 555, 780), stream=png_load)

    pdf_bytes = doc.tobytes(); doc.close()
    st.download_button("Download PDF", data=pdf_bytes, file_name=pdf_name, mime="application/pdf")
    st.success("PDF generated.")

    # Optional webhook
    if flow_url:
        try:
            if flow_payload_mode == "base64":
                payload = {"fileName": pdf_name, "content_b64": base64.b64encode(pdf_bytes).decode('utf-8')}
                r = requests.post(flow_url, json=payload, timeout=20)
            else:
                files = {"file": (pdf_name, pdf_bytes, "application/pdf")}
                data = {"fileName": pdf_name}
                r = requests.post(flow_url, data=data, files=files, timeout=20)
            st.info(f"Flow response: {r.status_code} {r.text[:200]}")
        except Exception as e:
            st.warning(f"Could not send to Flow: {e}")
