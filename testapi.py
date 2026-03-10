from google import genai

client = genai.Client(api_key='AIzaSyDJbG-UHaiOi9X_myovUcy1Bc3Y2pxdPxk')

response = client.models.generate_content(
    model="gemini-3-flash-preview",
    contents="Explain how AI works in a few words",
)

print(response.text)