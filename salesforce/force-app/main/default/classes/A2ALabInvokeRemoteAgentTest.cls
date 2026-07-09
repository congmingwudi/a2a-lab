/**
 * Tests for A2ALabInvokeRemoteAgent. Production deploys require running
 * tests with >=75% coverage, so this class ships with the invocable from
 * day one and covers the success, non-2xx, default-target, and bulk paths.
 */
@IsTest
private class A2ALabInvokeRemoteAgentTest {

    private class BridgeMock implements HttpCalloutMock {
        Integer statusCode;
        String body;
        HttpRequest lastRequest;

        BridgeMock(Integer statusCode, String body) {
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

    private static A2ALabInvokeRemoteAgent.Request makeRequest(
        String question, String target
    ) {
        A2ALabInvokeRemoteAgent.Request req = new A2ALabInvokeRemoteAgent.Request();
        req.question = question;
        req.sessionId = 'sess-1';
        req.target = target;
        return req;
    }

    @IsTest
    static void returnsAnswerOnSuccess() {
        BridgeMock mock = new BridgeMock(
            200,
            '{"text": "MCP standardizes tool access; A2A standardizes agent tasks.", "session_id": "sess-1"}'
        );
        Test.setMock(HttpCalloutMock.class, mock);

        Test.startTest();
        List<A2ALabInvokeRemoteAgent.Response> out = A2ALabInvokeRemoteAgent.ask(
            new List<A2ALabInvokeRemoteAgent.Request>{
                makeRequest('What is the difference between MCP and A2A?', 'claude-mcp')
            }
        );
        Test.stopTest();

        System.assertEquals(1, out.size());
        System.assert(out[0].answer.contains('MCP standardizes'), out[0].answer);
        System.assert(String.isNotBlank(out[0].traceId), 'trace id must be set');
        System.assert(
            mock.lastRequest.getEndpoint().endsWith('/invoke/claude-mcp'),
            'target must flow into the bridge path: ' + mock.lastRequest.getEndpoint()
        );
        System.assertEquals(
            out[0].traceId,
            mock.lastRequest.getHeader('X-Trace-Id'),
            'trace id must be forwarded as X-Trace-Id'
        );
    }

    @IsTest
    static void defaultsTargetWhenBlank() {
        BridgeMock mock = new BridgeMock(200, '{"text": "ok"}');
        Test.setMock(HttpCalloutMock.class, mock);

        Test.startTest();
        A2ALabInvokeRemoteAgent.ask(
            new List<A2ALabInvokeRemoteAgent.Request>{ makeRequest('hi', null) }
        );
        Test.stopTest();

        System.assert(
            mock.lastRequest.getEndpoint().endsWith('/invoke/claude-rest'),
            mock.lastRequest.getEndpoint()
        );
    }

    @IsTest
    static void reportsErrorOnNon2xx() {
        Test.setMock(HttpCalloutMock.class, new BridgeMock(502, 'bad gateway'));

        Test.startTest();
        List<A2ALabInvokeRemoteAgent.Response> out = A2ALabInvokeRemoteAgent.ask(
            new List<A2ALabInvokeRemoteAgent.Request>{ makeRequest('hi', 'claude-rest') }
        );
        Test.stopTest();

        System.assert(out[0].answer.contains('502'), out[0].answer);
    }

    @IsTest
    static void handlesBulkRequests() {
        Test.setMock(HttpCalloutMock.class, new BridgeMock(200, '{"text": "ok"}'));

        List<A2ALabInvokeRemoteAgent.Request> reqs =
            new List<A2ALabInvokeRemoteAgent.Request>();
        for (Integer i = 0; i < 3; i++) {
            reqs.add(makeRequest('q' + i, 'claude-rest'));
        }

        Test.startTest();
        List<A2ALabInvokeRemoteAgent.Response> out = A2ALabInvokeRemoteAgent.ask(reqs);
        Test.stopTest();

        System.assertEquals(3, out.size());
    }
}
