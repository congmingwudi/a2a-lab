/**
 * Tests for A2ALabInvokeAgentEngine. Production deploys require running
 * tests with >=75% coverage; covers the success, non-2xx, no-task,
 * default-path, rider-stamping, and bulk paths.
 */
@IsTest
private class A2ALabInvokeAgentEngineTest {

    private static final String TASK_JSON =
        '{"task": {"artifacts": [{"name": "answer", "parts": [{"text": "Omega operates in tech."}]}],'
        + '"status": {"state": "TASK_STATE_COMPLETED"}, "id": "t-1"}}';

    private class EngineMock implements HttpCalloutMock {
        Integer statusCode;
        String body;
        HttpRequest lastRequest;

        EngineMock(Integer statusCode, String body) {
            this.statusCode = statusCode;
            this.body = body;
        }

        public HttpResponse respond(HttpRequest req) {
            this.lastRequest = req;
            HttpResponse res = new HttpResponse();
            res.setStatusCode(this.statusCode);
            res.setBody(this.body);
            return res;
        }
    }

    private static A2ALabInvokeAgentEngine.Request makeRequest(
        String question, String enginePath
    ) {
        A2ALabInvokeAgentEngine.Request req = new A2ALabInvokeAgentEngine.Request();
        req.question = question;
        req.enginePath = enginePath;
        return req;
    }

    @IsTest
    static void returnsTaskArtifactTextOnSuccess() {
        EngineMock mock = new EngineMock(200, TASK_JSON);
        Test.setMock(HttpCalloutMock.class, mock);

        Test.startTest();
        List<A2ALabInvokeAgentEngine.Response> out = A2ALabInvokeAgentEngine.ask(
            new List<A2ALabInvokeAgentEngine.Request>{
                makeRequest('Research Omega, Inc.', null)
            }
        );
        Test.stopTest();

        System.assertEquals(1, out.size());
        System.assert(out[0].answer.contains('Omega operates'), out[0].answer);
        System.assert(String.isNotBlank(out[0].traceId), 'trace id must be set');
        System.assert(
            mock.lastRequest.getEndpoint().endsWith('/a2a/message:send'),
            'must call the A2A message:send binding: ' + mock.lastRequest.getEndpoint()
        );
        System.assertEquals('1.0', mock.lastRequest.getHeader('a2a-version'),
            'the version header is required — the handler rejects 0.3 default');
    }

    @IsTest
    static void stampsDelegationRiderAndMetadata() {
        EngineMock mock = new EngineMock(200, TASK_JSON);
        Test.setMock(HttpCalloutMock.class, mock);

        Test.startTest();
        A2ALabInvokeAgentEngine.ask(
            new List<A2ALabInvokeAgentEngine.Request>{ makeRequest('question', null) }
        );
        Test.stopTest();

        String sent = mock.lastRequest.getBody();
        System.assert(sent.contains('[A2A-LAB DELEGATION]'),
            'the D27 rider must ride the message text: ' + sent);
        System.assert(sent.contains('delegation-depth: 1'), sent);
        System.assert(sent.contains('"platform":"agentforce"'),
            'machine-readable delegation metadata must ride the message: ' + sent);
    }

    @IsTest
    static void reportsErrorOnNon2xx() {
        Test.setMock(HttpCalloutMock.class, new EngineMock(403, '{"error": {}}'));

        Test.startTest();
        List<A2ALabInvokeAgentEngine.Response> out = A2ALabInvokeAgentEngine.ask(
            new List<A2ALabInvokeAgentEngine.Request>{ makeRequest('hi', null) }
        );
        Test.stopTest();

        System.assert(out[0].answer.contains('403'), out[0].answer);
    }

    @IsTest
    static void reportsMissingTask() {
        Test.setMock(HttpCalloutMock.class, new EngineMock(200, '{"message": {}}'));

        Test.startTest();
        List<A2ALabInvokeAgentEngine.Response> out = A2ALabInvokeAgentEngine.ask(
            new List<A2ALabInvokeAgentEngine.Request>{ makeRequest('hi', null) }
        );
        Test.stopTest();

        System.assert(out[0].answer.contains('no task'), out[0].answer);
    }

    @IsTest
    static void handlesBulkAndCustomPath() {
        EngineMock mock = new EngineMock(200, TASK_JSON);
        Test.setMock(HttpCalloutMock.class, mock);

        List<A2ALabInvokeAgentEngine.Request> reqs =
            new List<A2ALabInvokeAgentEngine.Request>();
        for (Integer i = 0; i < 2; i++) {
            reqs.add(makeRequest('q' + i, '/v1beta1/projects/p/locations/l/reasoningEngines/42/a2a'));
        }

        Test.startTest();
        List<A2ALabInvokeAgentEngine.Response> out = A2ALabInvokeAgentEngine.ask(reqs);
        Test.stopTest();

        System.assertEquals(2, out.size());
        System.assert(
            mock.lastRequest.getEndpoint().contains('/reasoningEngines/42/a2a'),
            mock.lastRequest.getEndpoint()
        );
    }
}
