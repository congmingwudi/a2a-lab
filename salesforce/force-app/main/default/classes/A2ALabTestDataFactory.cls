@IsTest
public class A2ALabTestDataFactory {
    public static List<Account> accounts(Integer count, String prefix) {
        List<Account> records = new List<Account>();
        for (Integer i = 0; i < count; i++) {
            records.add(new Account(Name = prefix + ' ' + i));
        }
        insert records;
        return records;
    }
}
