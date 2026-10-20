name = "AceNix"
version = "0.1"

print(name,version)

with open("config.sys", "r") as file:
    content = file.read()

print(content)

rules = content

lines = rules.splitlines()


for systemlines in lines:
    print(systemlines)