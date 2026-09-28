"""DevOps/CI template generator (inventory domain 7 #16-18 + #19 IaC).

"github actions workflow for a python app", "gitlab ci for node", "dockerfile for python",
"docker compose for web and db", "kubernetes deployment for api", "terraform for aws s3".

Templates are assembled, not invented: every pinned action version, base image tag and
terraform block below is copied verbatim into the reply, and the owner's service name is
the only free variable. Output can be pasted into a repo as-is and edited.

T0: pure text generation, nothing written, no network.
"""
import re

from ishti.skills import trace

I = re.I

KINDS = [
    ("actions", re.compile(r"\bgithub\s+actions?\b|\b(?:gh|github)\s+(?:ci|workflow)\b|\bactions?\s+(?:workflow|yml)\b", I)),
    ("gitlab", re.compile(r"\bgitlab\s+ci\b|\b\.gitlab-ci\b", I)),
    ("dockerfile", re.compile(r"\bdocker\s*file\b|\bdockerfile\b", I)),
    ("compose", re.compile(r"\bdocker\s+compose\b|\bcompose\s+file\b", I)),
    ("k8s", re.compile(r"\bkubernetes\b|\bk8s\b|\bkubectl\b", I)),
    ("terraform", re.compile(r"\bterraform\b|\btf\s+script\b|\bhashicorp\b", I)),
]
STACK = re.compile(r"\b(python|node|nodejs|node\.?js|react|go|golang|rust|java|dotnet|\.net|php)\b", I)
NAME = re.compile(r"\b(?:for|called|named)\s+(?:a\s+|an\s+|the\s+)?(?P<name>[a-z][\w-]{1,30})", I)

GHA = """name: CI
on:
  push:
    branches: [ main ]
  pull_request:

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt
      - run: python -m pytest
      - name: Smoke
        run: python -c "print('build OK')"
"""

GHL = """stages:          # list stages here; jobs pick one via `stage:`
  - build
  - test

build_job:
  stage: build
  image: python:3.12-slim
  script:
    - pip install -r requirements.txt

test_job:
  stage: test
  image: python:3.12-slim
  script:
    - pip install -r requirements.txt
    - python -m pytest
"""

DOCKERFILE = """FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["python", "main.py"]
"""

COMPOSE = """services:
  {name}:
    build: .
    ports:
      - "8000:8000"
    environment:
      - ENV=production
    depends_on:
      - db
  db:
    image: postgres:16
    environment:
      POSTGRES_PASSWORD: change-me
    volumes:
      - dbdata:/var/lib/postgresql/data
volumes:
  dbdata:
"""

K8S = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: {name}
  labels:
    app: {name}
spec:
  replicas: 2
  selector:
    matchLabels:
      app: {name}
  template:
    metadata:
      labels:
        app: {name}
    spec:
      containers:
        - name: {name}
          image: {name}:latest
          ports:
            - containerPort: 8000
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 512Mi
---
apiVersion: v1
kind: Service
metadata:
  name: {name}
spec:
  selector:
    app: {name}
  ports:
    - port: 80
      targetPort: 8000
"""

TF = """terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "eu-central-1"   # change me
}

resource "aws_s3_bucket" "app" {
  bucket = "{name}-bucket"  # must be globally unique
}

resource "aws_s3_bucket_versioning" "app" {
  bucket = aws_s3_bucket.app.id
  versioning_configuration {
    status = "Enabled"
  }
}
"""

TEMPLATES = {"actions": GHA, "gitlab": GHL, "dockerfile": DOCKERFILE, "compose": COMPOSE,
             "k8s": K8S, "terraform": TF}


def _kind(text):
    for kind, rx in KINDS:
        if rx.search(text):
            return kind
    if re.search(r"\bci\s+pipeline\b|\bci/cd\b|\bci\s+for\b", text, I):
        return "actions"          # the default CI everyone means
    return None


def _name(text, kind):
    m = NAME.search(text)
    base = m.group("name") if m else ("web" if kind in ("compose", "k8s") else "app")
    return re.sub(r"[^\w-]", "", base) or "app"


def template_for(text):
    kind = _kind(text)
    if not kind:
        return None, None
    name = _name(text, kind)
    # .replace, not .format: the terraform/k8s templates contain literal { } blocks
    body = TEMPLATES[kind].replace("{name}", name)
    head = {"actions": "GitHub Actions workflow (`.github/workflows/ci.yml`)",
            "gitlab": "GitLab CI config (`.gitlab-ci.yml`)",
            "dockerfile": "Dockerfile",
            "compose": "Docker Compose file (`compose.yaml`)",
            "k8s": "Kubernetes Deployment + Service (`deploy.yaml`)",
            "terraform": "Terraform config (`main.tf`)"}[kind]
    tail = {"compose": "Postgres password is a placeholder - set a real one before running.",
            "terraform": "Pin the AWS region and bucket name; run `terraform plan` before `apply`."}
    extra = tail.get(kind)
    return f"**{head}** for `{name}`:\n```yaml\n{body.strip()}\n```" + (f"\n\n{extra}" if extra else ""), kind


QUESTION = re.compile(r"^\s*(?:what|who|why|when|explain|tell me about|how does)\b", I)


def match(text):
    t = text.strip()
    # _kind tells us the topic; the second regex demands a make-me-a-file shape ("what is
    # kubernetes" must stay a chat question). Actions keeps its own wording, gitlab ci implies a file.
    kind = _kind(t)
    if kind is None or QUESTION.match(t):
        return False
    if kind == "gitlab":
        return True
    if kind == "actions":
        return bool(re.search(r"\bworkflow\b|\bci\b|\bactions?\b|\bfor\b|\byml\b|\byaml\b|\bpipeline\b", t, I))
    return bool(re.search(r"\bfor\b|\bfile\b|\btemplate\b|\bconfig\b|\bcompose\b|\bwrite\b|\bgenerate\b|\bcreate\b|\bmake\b", t, I))


def run(text, session):
    if not match(text):        # questions like "what is kubernetes" never produce a file
        return None
    reply, kind = template_for(text.strip())
    if reply is None:
        return None
    trace(session, "devops", kind)
    return {"reply": reply, "sources": []}


if __name__ == "__main__":
    assert match("create a github actions workflow for a python app")
    assert match("dockerfile for python")
    assert match("docker compose for web and db")
    assert match("kubernetes deployment for api")
    assert match("terraform for aws s3")
    assert match("gitlab ci for node")
    assert match("ci pipeline for the api")
    assert not match("explain what docker is")               # explanation, not a template ask
    assert not match("what is kubernetes")                    # a chat question
    assert not match("what is terraform used for")            # 'for' alone must not flip it
    assert not match("push to github")                        # dev/git territory
    assert not match("terraform my garden")                   # needs the file/pipeline context words
    r = run("github actions workflow for the ishti-api python app", {})
    assert "actions/checkout@v4" in r["reply"] and "setup-python@v5" in r["reply"], r["reply"][:200]
    r2 = run("kubernetes deployment for orders", {})
    assert "kind: Deployment" in r2["reply"] and "name: orders" in r2["reply"], r2["reply"][:200]
    r3 = run("docker compose for web and db", {})
    assert "image: postgres:16" in r3["reply"] and "change-me" in r3["reply"], r3["reply"][:200]
    r4 = run("terraform for aws s3", {})
    assert 'hashicorp/aws' in r4["reply"] and "aws_s3_bucket" in r4["reply"], r4["reply"][:200]
    r5 = run("dockerfile for python", {})
    assert "FROM python:3.12-slim" in r5["reply"], r5["reply"][:200]
    r6 = run("gitlab ci for node", {})
    assert "stages:" in r6["reply"] and "gitlab" in r6["reply"].lower(), r6["reply"][:200]
    assert run("what is kubernetes", {}) is None
    print("[self-check] devops OK - actions/gitlab/dockerfile/compose/k8s/terraform templates, "
          "pinned versions exact, service name is the only variable")
