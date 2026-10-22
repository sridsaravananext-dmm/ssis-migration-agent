
CREATE OR REPLACE PROCEDURE update_customer_status AS
BEGIN
    UPDATE customers
    SET status = 'ACTIVE'
    WHERE customer_id = 100;
EXCEPTION
    WHEN OTHERS THEN
        RAISE;
END;
/

SELECT customer_id
FROM customers;
