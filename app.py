import base64
import io
import json
import math
import os
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from docxtpl import DocxTemplate
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

st.set_page_config(page_title="Eurofins KCTL RF Inspector", page_icon="📡", layout="wide")


@st.cache_data
def extract_marker_data(image_bytes: bytes, api_key: str) -> dict:
    """스펙트럼 분석기 이미지 우측 상단 Mkr1 마커를 OpenAI 5.6 Luna Vision으로 파싱한다."""
    client = OpenAI(api_key=api_key)
    b64_str = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:image/png;base64,{b64_str}"

    response = client.chat.completions.create(
        model="gpt-5.6-luna",
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "이 스펙트럼 분석기 화면 우측 상단의 녹색 텍스트 Mkr1 영역에서 "
                            "주파수 값/단위와 전력 값/단위를 읽어 다음 JSON 스키마로만 응답하라: "
                            '{"freq_val": number, "freq_unit": "GHz"|"MHz", '
                            '"power_val": number, "power_unit": "uW"}'
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
    )
    return json.loads(response.choices[0].message.content)


def calc_rf_metrics(marker: dict, cf_db: float, limit_dbuv: float) -> dict:
    """마커 파싱 결과로부터 RF 물리량 및 규격 판정을 계산한다."""
    freq_mhz = marker["freq_val"] * 1000 if marker["freq_unit"] == "GHz" else marker["freq_val"]
    power_uw = marker["power_val"]

    dbm = 10 * math.log10(power_uw / 1000)
    dbuv_m = dbm + 107.0 + cf_db
    margin = limit_dbuv - dbuv_m
    verdict = "PASS" if margin >= 0 else "FAIL"

    return {
        "freq_mhz": round(freq_mhz, 2),
        "power_uw": round(power_uw, 2),
        "dbm": round(dbm, 2),
        "dbuv_m": round(dbuv_m, 2),
        "limit": round(limit_dbuv, 2),
        "margin": round(margin, 2),
        "verdict": verdict,
    }

with st.sidebar:
    st.title("📡 KCTL RF 계측 Inspector")

    api_key = st.text_input(
        "OpenAI API Key",
        type="password",
        value=os.getenv("OPENAI_API_KEY", ""),
    )
    st.caption("AI Engine: OpenAI 5.6 Luna Vision")

    tester = st.text_input("시험 담당자", value="홍길동 선임연구원")
    reviewer = st.text_input("기술 검토자", value="김선임 기술책임자")
    sample_name = st.text_input("시료명(EUT)", value="EUT-2026-BLE-MODULE")

    standard = st.selectbox(
        "시험 규격 선택",
        ["FCC Part 15 Subpart B Class B (3 m)", "CISPR 32 Class B (3 m)", "KN 32"],
    )

    cf_db = st.slider("안테나 보정계수 (CF)", min_value=15.0, max_value=40.0, value=28.5, step=0.5)
    limit_dbuv = st.slider("규격 기준치 (Limit)", min_value=120.0, max_value=150.0, value=140.0, step=1.0)

st.title("📡 Eurofins KCTL RF 계측 자동화 대시보드")
st.markdown("스펙트럼 분석기 이미지를 업로드하면 AI가 마커 데이터를 추출하고, RF 규격 판정 및 워드 성적서를 자동 발행합니다.")

uploaded_files = st.file_uploader(
    "스펙트럼 분석기 계측 이미지 업로드",
    accept_multiple_files=True,
    type=["png", "jpg"],
)

load_sample = st.button("📂 기본 샘플 3종(img_1~3) 일괄 불러오기")

if load_sample:
    st.session_state["image_sources"] = [
        (fname, open(fname, "rb").read()) for fname in ["img_1.png", "img_2.png", "img_3.png"]
    ]
elif uploaded_files:
    st.session_state["image_sources"] = [(f.name, f.getvalue()) for f in uploaded_files]

image_sources = st.session_state.get("image_sources", [])

if image_sources:
    if not api_key:
        st.warning("사이드바에 OpenAI API Key를 입력하세요.")
        st.stop()

    results = []
    for idx, (name, img_bytes) in enumerate(image_sources, start=1):
        try:
            marker = extract_marker_data(img_bytes, api_key)
            metrics = calc_rf_metrics(marker, cf_db, limit_dbuv)
            results.append({"no": idx, "image_name": name, **metrics})
        except Exception as e:
            st.error(f"오류 내용: {e}")

    st.session_state["analysis_results"] = results

results = st.session_state.get("analysis_results", [])

if results:
    total = len(results)
    pass_cnt = sum(1 for r in results if r["verdict"] == "PASS")
    fail_cnt = total - pass_cnt
    overall_verdict = "PASS" if fail_cnt == 0 else "FAIL"

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("총 측정 건수", total)
    col2.metric("PASS 건수", pass_cnt, delta=pass_cnt, delta_color="normal")
    col3.metric("FAIL 건수", fail_cnt, delta=fail_cnt, delta_color="inverse")
    with col4:
        st.markdown("종합 판정")
        verdict_color = "#00A651" if overall_verdict == "PASS" else "#E03A3A"
        st.markdown(
            f"<h2 style='color:{verdict_color}; margin-top:-10px;'>{overall_verdict}</h2>",
            unsafe_allow_html=True,
        )

    df = pd.DataFrame(results)

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=df["freq_mhz"],
            y=df["dbuv_m"],
            marker_color=["#003399" if v == "PASS" else "#E03A3A" for v in df["verdict"]],
            customdata=df["margin"],
            hovertemplate=(
                "주파수: %{x} MHz<br>측정값: %{y} dBµV/m<br>마진: %{customdata} dB<extra></extra>"
            ),
        )
    )
    fig.add_hline(
        y=limit_dbuv,
        line_dash="dash",
        line_color="red",
        annotation_text="FCC Limit 기준",
        annotation_position="top left",
    )
    fig.update_layout(
        xaxis_title="주파수 (MHz)",
        yaxis_title="측정 전계강도 (dBµV/m)",
    )
    st.plotly_chart(fig, use_container_width=True)

    display_df = df.rename(
        columns={
            "no": "No",
            "image_name": "이미지명",
            "freq_mhz": "주파수(MHz)",
            "power_uw": "측정전력(µW)",
            "dbm": "dBm",
            "dbuv_m": "측정값(dBµV/m)",
            "limit": "Limit",
            "margin": "마진(dB)",
            "verdict": "판정",
        }
    )[["No", "이미지명", "주파수(MHz)", "측정전력(µW)", "dBm", "측정값(dBµV/m)", "Limit", "마진(dB)", "판정"]]
    st.dataframe(display_df, use_container_width=True)

    remarks = st.text_area("비고", value="")

    try:
        now = datetime.now()
        doc = DocxTemplate("report_template.docx")
        context = {
            "doc_no": f"KCTL-RE-{now.strftime('%Y%m%d%H%M%S')}",
            "test_date": now.strftime("%Y-%m-%d"),
            "test_name": "방사성 방출 (RE) 측정 결과 보고서",
            "tester": tester,
            "standard": standard,
            "sample_name": sample_name,
            "cf_db": cf_db,
            "reviewer": reviewer,
            "remarks": remarks,
            "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "total": total,
            "pass_cnt": pass_cnt,
            "fail_cnt": fail_cnt,
            "verdict": overall_verdict,
            "r": [
                {
                    "no": r["no"],
                    "freq_mhz": r["freq_mhz"],
                    "power_uw": r["power_uw"],
                    "dbm": r["dbm"],
                    "dbuv_m": r["dbuv_m"],
                    "limit": r["limit"],
                    "margin": r["margin"],
                    "verdict": r["verdict"],
                }
                for r in results
            ],
        }
        doc.render(context)

        buffer = io.BytesIO()
        doc.save(buffer)
        buffer.seek(0)

        st.download_button(
            label="📥 공식 시험성적서(.docx) 다운로드",
            data=buffer,
            file_name=f"KCTL_RE_Report_{now.strftime('%Y%m%d')}.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    except Exception as e:
        st.error(f"오류 내용: {e}")
