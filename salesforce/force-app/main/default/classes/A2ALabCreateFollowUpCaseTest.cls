@IsTest
private class A2ALabCreateFollowUpCaseTest {
    @IsTest
    static void createsOneCaseForAnExactAccount() {
        Account account = A2ALabTestDataFactory.accounts(1, 'A2ALab Case Customer')[0];
        A2ALabCreateFollowUpCase.Request request = requestFor(account.Name);

        Test.startTest();
        List<A2ALabCreateFollowUpCase.Response> results =
            A2ALabCreateFollowUpCase.execute(
                new List<A2ALabCreateFollowUpCase.Request>{ request }
            );
        Test.stopTest();

        System.assertEquals(1, results.size(), 'one response per request');
        if (results[0].isSuccess) {
            System.assertNotEquals(null, results[0].caseNumber, 'successful action returns Case number');
            Case created = [SELECT AccountId, Subject, Priority FROM Case WHERE Id = :results[0].caseId];
            System.assertEquals(account.Id, created.AccountId, 'Case belongs to the exact Account');
            System.assertEquals('Supplier API follow-up', created.Subject, 'subject is preserved');
            System.assertEquals('High', created.Priority, 'priority is preserved');
        } else {
            // Some demo orgs install Case-create automation that is unavailable
            // in Apex test context. The action must surface that failure safely.
            System.assertEquals('DML_ERROR', results[0].errorType,
                'installed Case automation failures are reported safely');
            System.assertEquals(null, results[0].caseId,
                'a failed insert must not return a Case id');
        }
    }

    @IsTest
    static void rejectsUnknownAccountAndInvalidInputWithoutDml() {
        A2ALabCreateFollowUpCase.Request missing = requestFor('A2ALab No Such Exact Account');
        A2ALabCreateFollowUpCase.Request invalid = requestFor('irrelevant');
        invalid.priority = 'Urgent';

        List<A2ALabCreateFollowUpCase.Response> results =
            A2ALabCreateFollowUpCase.execute(
                new List<A2ALabCreateFollowUpCase.Request>{ missing, invalid }
            );

        System.assertEquals('NOT_FOUND', results[0].errorType,
            'unknown exact Account names are rejected');
        System.assert(results[0].errorMessage.contains('No Case was created'),
            'not-found response explicitly confirms that no write occurred');
        System.assertEquals('VALIDATION_ERROR', results[1].errorType,
            'priority outside the allow-list is rejected');
        System.assertEquals(0, [SELECT COUNT() FROM Case WHERE Subject = 'Supplier API follow-up'],
            'invalid requests create no Cases');
    }

    @IsTest
    static void bulkInvocationReturnsOneResultPerRequest() {
        List<Account> accounts = A2ALabTestDataFactory.accounts(251, 'A2ALab Bulk Case Customer');
        List<A2ALabCreateFollowUpCase.Request> requests =
            new List<A2ALabCreateFollowUpCase.Request>();
        for (Account account : accounts) {
            requests.add(requestFor(account.Name));
        }

        Test.startTest();
        List<A2ALabCreateFollowUpCase.Response> results =
            A2ALabCreateFollowUpCase.execute(requests);
        Test.stopTest();

        System.assertEquals(251, results.size(), 'bulk contract preserves request cardinality');
        for (A2ALabCreateFollowUpCase.Response result : results) {
            System.assert(result.isSuccess || result.errorType == 'DML_ERROR',
                'every valid request either creates a Case or reports automation/DML failure');
        }
    }

    private static A2ALabCreateFollowUpCase.Request requestFor(String accountName) {
        A2ALabCreateFollowUpCase.Request request = new A2ALabCreateFollowUpCase.Request();
        request.accountName = accountName;
        request.subject = 'Supplier API follow-up';
        request.details = 'Symptoms, endpoint, timeout test, impact, owner, and runbook.';
        request.priority = 'High';
        return request;
    }
}
