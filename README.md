
<p align="center">
  <img src="banner.png" width="100%">
</p>

<h1 align="center">Reel Recommendation System</h1>
<p align="center">A Hybrid ML-Powered Personalized Feed Engine</p>

---

# 🌟 1. Project Overview
A modern short-video recommendation engine combining Content-Based Filtering, Collaborative Filtering, Popularity Modeling, Engagement Metrics, and Exploration-based Diversity.

---

# 🌟 2. Dataset Overview
The dataset includes:
- video_id
- niche/category
- title, clean_title
- tags, clean_tags
- duration_sec
- views_total, likes, comments
- retention_rate
- first_3_sec_engagement
- category_id

---

# 🌟 3. System Architecture

<p align="center">
<pre>
Flutter App → FastAPI Backend → Recommender Engine → MongoDB + Redis
</pre>
</p>

---

# 🌟 4. Recommendation Pipeline

## 1️⃣ Candidate Generation
- Tag similarity
- Item–item co-engagement
- Popularity signals
- Freshness boosts
- Niche diversity

## 2️⃣ Hybrid Scoring
Score =  
0.5 * ContentSimilarity  
+ 0.3 * CollaborativeFiltering  
+ 0.1 * Popularity  
+ 0.1 * Exploration  

## 3️⃣ Diversity Filters
- Limit repeated creators  
- Ensure niche variety  
- Prioritize novelty  

---

# 🌟 5. Features

### 🔍 Content-Based Filtering
TF-IDF / tag vectors + cosine similarity  

### 👥 Collaborative Filtering
User–item implicit matrix  
Item–item similarity  

### ⚡ Popularity Modeling
views_total, likes, retention rate, first-3-sec engagement  

### 🎲 Exploration
Randomness + diversity  

---

# 🌟 6. Repository Structure
```
root/
│ backend/
│   api/
│   recommender/
│   database/
│   security/
│ ml/
│ data/
│ README.md
```

---

# 🌟 7. API Documentation

### GET /feed
Returns personalized ranked feed.

### POST /events
Logs user watch/like/skip activity.

### POST /admin/reindex
Refreshes model scoring caches.

---

# 🌟 8. Security
- JWT Authentication  
- HTTPS enforced  
- Rate-limiting via Redis  
- No PII stored  

---

# 🌟 9. Setup Instructions
```
git clone <repo>
cd repo
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn backend.main:app --reload
```

---

# 🌟 10. Contribution Guide
- Use feature branches  
- Descriptive commit messages  
- Submit PRs  

---

# 🌟 11. Future Enhancements
- Deep embeddings  
- Neural Collaborative Filtering  
- Transformers for ranking  
- RL-based personalization  

---

# 🌟 12. Credits
IE Code NITK 2025–26  
Team Members & Mentors  

---
