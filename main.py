from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def greet():
    return "Bienvenido"
    
def test():
    return 1