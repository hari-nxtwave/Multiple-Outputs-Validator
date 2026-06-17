# Sum of Array (single-output — should be REJECTED)

Given an array of `n` integers, return their sum. There is exactly one correct
answer for any input.

Implement:

```java
class Solution {
    public long arraySum(int[] arr) { ... }
}
```

Input format: the first line is `n`, followed by `n` integers.
Output: print the sum.

```java
import java.util.*;

public class Main {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);
        int n = sc.nextInt();
        int[] arr = new int[n];
        for (int i = 0; i < n; i++) arr[i] = sc.nextInt();
        Solution sol = new Solution();
        System.out.println(sol.arraySum(arr));
    }
}
```
