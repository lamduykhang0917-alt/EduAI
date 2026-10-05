# Kết quả đánh giá KeywordIntentClassifier (số liệu thật)

Chạy bằng: `python -m ai_service.evaluate` (từ thư mục `eduai/`)

Phương pháp: classifier được xây dựng lại chỉ từ các mẫu câu trong
`dataset/splits/train.json` + `val.json` (không bao gồm `test.json`), sau đó
đánh giá trên tập `test.json` (giữ lại, chưa từng thấy khi "huấn luyện").
Đây là số liệu thật, đo trực tiếp bằng code, không phải số liệu ví dụ.

| Model | Accuracy | Precision (macro) | Recall (macro) | F1-score (macro) |
|---|---|---|---|---|
| KeywordIntentClassifier | 58.33% | 54.17% | 58.33% | 55.56% |
| PhoBERTIntentClassifier | Chưa thực hiện — chưa có checkpoint đã fine-tune | — | — | — |

Test set: 12 câu (1 câu/intent, 12 intent). Chi tiết từng câu và ma trận
nhầm lẫn đầy đủ nằm trong `eval_report.json` (cùng thư mục).

Nhận xét để đưa vào Chương 5 báo cáo:
- Độ chính xác còn thấp vì dataset hiện tại rất nhỏ (mỗi intent chỉ có
  vài mẫu câu khai báo trong `intents.json`), đúng với hạn chế đã nêu ở
  mục 6.2 của báo cáo (KeywordIntentClassifier "có độ chính xác hạn chế
  với các câu hỏi diễn đạt theo cách chưa được khai báo trước").
- Các lỗi phân loại tập trung ở các intent có ngữ nghĩa gần nhau (ví dụ
  `result` bị nhận nhầm thành `exam`, `learning_recommendation` nhầm
  thành `learning_progress`) — đây là điểm mà PhoBERT (hiểu ngữ nghĩa)
  được kỳ vọng cải thiện so với so khớp từ khóa thuần túy.
- Dòng PhoBERTIntentClassifier để trống số liệu vì mô hình chưa được
  fine-tune (đúng ghi chú ở đầu Chương 5 của báo cáo và mục 47 của spec
  dự án — không bịa số liệu khi chưa thực sự đo được).
