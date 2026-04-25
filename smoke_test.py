import requests, json

r = requests.get('https://se-payment-verification-api.service.external.usea2.aws.prodigaltech.com/openapi/json', timeout=8)
spec = r.json()

for path, methods in spec.get('paths', {}).items():
    print("PATH:", path)
    for method, details in methods.items():
        summary = details.get('summary', '')
        print("  METHOD:", method.upper(), "|", summary)
        rb = details.get('requestBody', {})
        if rb:
            for ct, schema_info in rb.get('content', {}).items():
                schema_str = json.dumps(schema_info.get('schema', {}), indent=2)
                print("  Request body schema:", schema_str[:600])
        for code, resp in details.get('responses', {}).items():
            for ct2, s2 in resp.get('content', {}).items():
                schema_str2 = json.dumps(s2.get('schema', {}))
                print("  Response", code, ":", schema_str2[:300])
    print()

