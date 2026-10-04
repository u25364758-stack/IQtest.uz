"use strict";

const tg = window.Telegram?.WebApp;
if (tg) { tg.ready(); tg.expand(); }

let questions = [];
let answers = [];
let currentQuestion = 0;
let attemptId = null;
let startTime = null;
let timerInterval = null;
let busy = false;

const $ = (id) => document.getElementById(id);
const errorBox = $("error");

async function api(path, body) {
  if (!tg?.initData) throw new Error("Telegram Mini App sessiyasi topilmadi. Bot ichidan qayta oching.");
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Telegram-Init-Data": tg.initData },
    body: JSON.stringify(body)
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.message || result.error || "So‘rov bajarilmadi. Qayta urinib ko‘ring.");
  return result;
}

function showError(message) { errorBox.textContent = message; }

$("profileForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;
  const fullName = $("fullName").value.trim();
  const age = Number($("age").value);
  const gender = $("gender").value;
  if (fullName.length < 2 || !Number.isInteger(age) || age < 5 || age > 120 || !gender) {
    showError("Ma’lumotlarni to‘g‘ri to‘ldiring."); return;
  }
  busy = true; $("startButton").disabled = true; showError("");
  try {
    await api("/api/profile", { full_name: fullName, age, gender });
    const data = await api("/api/questions", {});
    if (!Array.isArray(data.questions) || data.questions.length !== 25) throw new Error("Test savollari yuklanmadi.");
    questions = data.questions; answers = Array(25).fill(null); attemptId = data.attempt_id;
    startTime = Date.now(); currentQuestion = 0;
    $("profileScreen").classList.add("hidden"); $("testScreen").classList.remove("hidden");
    timerInterval = setInterval(updateTimer, 1000); showQuestion();
  } catch (error) { showError(error.message); }
  finally { busy = false; $("startButton").disabled = false; }
});

function updateTimer() {
  const seconds = Math.floor((Date.now() - startTime) / 1000);
  $("timer").textContent = `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

function showQuestion() {
  const q = questions[currentQuestion];
  $("questionNumber").textContent = `${currentQuestion + 1} / 25`;
  $("question").textContent = q.question;
  $("progressBar").style.width = `${((currentQuestion + 1) / 25) * 100}%`;
  const options = $("options"); options.replaceChildren();
  for (const key of ["A", "B", "C", "D"]) {
    const button = document.createElement("button");
    button.type = "button"; button.className = "option";
    button.textContent = `${key}. ${q.options[key]}`;
    button.setAttribute("aria-pressed", answers[currentQuestion] === key ? "true" : "false");
    if (answers[currentQuestion] === key) button.classList.add("selected");
    button.addEventListener("click", () => {
      answers[currentQuestion] = key;
      for (const item of options.children) { item.classList.remove("selected"); item.setAttribute("aria-pressed", "false"); }
      button.classList.add("selected"); button.setAttribute("aria-pressed", "true"); $("nextButton").disabled = false;
    });
    options.append(button);
  }
  $("nextButton").disabled = !answers[currentQuestion];
  $("nextButton").textContent = currentQuestion === 24 ? "TESTNI YAKUNLASH" : "KEYINGI";
}

$("nextButton").addEventListener("click", async () => {
  if (busy || !answers[currentQuestion]) return;
  if (currentQuestion < 24) { currentQuestion++; showQuestion(); return; }
  busy = true; $("nextButton").disabled = true;
  try {
    const result = await api("/api/finish", { attempt_id: attemptId, answers });
    clearInterval(timerInterval);
    const mins = Math.floor(result.duration / 60), secs = result.duration % 60;
    const p = document.createElement("p");
    p.append("To‘g‘ri javoblar: "); const score = document.createElement("strong"); score.textContent = `${result.correct}/25`; p.append(score);
    p.append(document.createElement("br"), document.createElement("br"), "Sarflangan vaqt: ");
    const time = document.createElement("strong"); time.textContent = `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`; p.append(time);
    p.append(document.createElement("br"), document.createElement("br"), "IQ uslubidagi ball: ");
    const iq = document.createElement("strong"); iq.textContent = String(result.iq); p.append(iq);
    $("resultText").replaceChildren(p); $("testScreen").classList.add("hidden"); $("resultScreen").classList.remove("hidden");
  } catch (error) { showError(error.message); $("nextButton").disabled = false; }
  finally { busy = false; }
});

$("restartButton").addEventListener("click", () => window.location.reload());
